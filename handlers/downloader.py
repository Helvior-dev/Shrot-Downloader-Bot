import os
import re
import time
import logging
import asyncio
import urllib.parse
from aiogram import Router, F, Bot
from aiogram.enums import ParseMode, ChatAction
from aiogram.types import Message, InputMediaPhoto, InputMediaVideo, FSInputFile

from downloader import download_media, cleanup_media, is_supported_url, IMAGE_EXTENSIONS, FileTooLargeError, DownloadError, MediaInfo
from keyboards.reply import get_main_keyboard
from utils.auth import is_user_allowed

logger = logging.getLogger(__name__)

router = Router()

URL_REGEX = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)

# Anti-spam lock: set of user_ids currently downloading
active_downloads = set()

def _download_error_message(error: DownloadError) -> str:
    """Return a brief message without exposing extractor responses or URLs."""
    details = str(error).lower()
    if "pin type could not be verified" in details:
        return "Could not verify this Pinterest pin. Try again later."
    if "refusing to send its preview" in details or "video could not be downloaded" in details:
        return "This video is unavailable for download."
    if any(word in details for word in ("private", "not available", "unavailable", "removed", "404", "login required")):
        return "This media is unavailable or private. Check the link."
    if any(word in details for word in ("429", "rate limit", "too many requests", "403", "forbidden")):
        return "The platform is temporarily restricting downloads. Try again later."
    return "Could not download this media. Check the link or try again later."

def _safe_error_details(error: Exception) -> str:
    """Keep diagnostic text in logs without logging full URLs or bot tokens."""
    details = re.sub(r'https?://[^\s<>"\']+', '[URL]', str(error))
    details = re.sub(r'\b\d{6,}:[A-Za-z0-9_-]{20,}\b', '[TOKEN]', details)
    return details[:300]

async def send_carousel(message: Message, media_info: MediaInfo) -> None:
    """Send photo/video albums in Telegram's groups of up to ten items."""
    for offset in range(0, len(media_info.media_paths), 10):
        paths = media_info.media_paths[offset:offset + 10]
        if len(paths) == 1:
            file = FSInputFile(paths[0])
            if os.path.splitext(paths[0])[1].lower() in IMAGE_EXTENSIONS:
                await message.answer_photo(photo=file)
            else:
                await message.answer_video(video=file)
            continue
        media_group = []
        for path in paths:
            kind = InputMediaPhoto if os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS else InputMediaVideo
            media_group.append(kind(media=FSInputFile(path)))
        await message.answer_media_group(media=media_group)

@router.message(F.text)
async def process_video_link(message: Message, bot: Bot) -> None:
    if message.from_user is None:
        return
    if not is_user_allowed(message.from_user):
        await message.answer(
            f"<b>Access denied</b>\n\n"
            f"Your Telegram ID: <code>{message.from_user.id}</code>\n"
            f"Ask the bot owner to add this ID to <code>ALLOWED_USERS</code>.",
            parse_mode=ParseMode.HTML
        )
        return

    text = message.text.strip()

    user_id = message.from_user.id
    if user_id in active_downloads:
        await message.answer("Your previous download is still running. Please wait.")
        return

    match = URL_REGEX.search(text)
    if not match:
        await message.answer("Send a TikTok, Instagram, Pinterest, or X link.", reply_markup=get_main_keyboard())
        return
    url = match.group(0).rstrip(".,;!?)")
    if not is_supported_url(url):
        await message.answer("Unsupported link. Send a TikTok, Instagram, Pinterest, or X link.", reply_markup=get_main_keyboard())
        return

    active_downloads.add(user_id)
    platform = urllib.parse.urlsplit(url).hostname or "unknown"
    progress_msg = None
    sticker_msg = None
    sticker_id = os.getenv("LOADING_STICKER_ID", "").strip()
    media_info = None

    try:
        if sticker_id:
            try:
                sticker_msg = await message.answer_sticker(sticker=sticker_id)
                progress_msg = await message.answer("Downloading media...")
            except Exception as e:
                logger.warning("Failed to send sticker (%s): %s", type(e).__name__, _safe_error_details(e))
                progress_msg = await message.answer("Downloading media...")
        else:
            progress_msg = await message.answer("Downloading media...")

        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_VIDEO)

        last_update_time = time.time()

        async def update_progress(percent_str: str):
            nonlocal last_update_time
            now = time.time()
            # Update at most once every 3.5 seconds to avoid FloodWait
            if now - last_update_time > 3.5 and progress_msg:
                try:
                    await progress_msg.edit_text(f"Downloading media: {percent_str}")
                    last_update_time = now
                except Exception:
                    pass

        try:
            media_info = await download_media(url, format_type="video", progress_callback=update_progress)
        except FileTooLargeError as e:
            logger.info("Download limit reached for %s (%s): %s", platform, type(e).__name__, _safe_error_details(e))
            if progress_msg:
                await progress_msg.edit_text(
                    "Download limit exceeded. The file or request is too large."
                )
            return
        except DownloadError as e:
            logger.warning("Download failed for %s (%s): %s", platform, type(e).__name__, _safe_error_details(e))
            if progress_msg:
                await progress_msg.edit_text(_download_error_message(e))
            return
        except Exception as e:
            logger.error("Unexpected download error for %s (%s): %s", platform, type(e).__name__, _safe_error_details(e))
            if progress_msg:
                await progress_msg.edit_text("An unexpected error occurred.")
            return

        if progress_msg:
            try:
                await progress_msg.edit_text("Uploading to Telegram...")
            except Exception:
                pass

        if media_info.media_type == "carousel" and media_info.media_paths:
            await send_carousel(message, media_info)

        elif media_info.media_type == "photo" and media_info.filepath:
            photo_file = FSInputFile(media_info.filepath)
            await message.answer_photo(
                photo=photo_file,
            )

        elif media_info.media_type == "audio" and media_info.filepath:
            audio_file = FSInputFile(media_info.filepath)
            await message.answer_audio(
                audio=audio_file,
            )

        elif media_info.filepath:
            video_file = FSInputFile(media_info.filepath)
            await message.answer_video(
                video=video_file,
                width=media_info.width or 0,
                height=media_info.height or 0,
                supports_streaming=True
            )

        # Cleanup UI progress messages
        if sticker_msg:
            try:
                await sticker_msg.delete()
            except Exception:
                pass
        if progress_msg:
            try:
                await progress_msg.delete()
            except Exception:
                pass

    except Exception as e:
        logger.error("Telegram upload failed for %s (%s): %s", platform, type(e).__name__, _safe_error_details(e))
        if progress_msg:
            try:
                await progress_msg.edit_text("Could not send the file to Telegram.")
            except Exception:
                pass
    finally:
        active_downloads.discard(user_id)
        if media_info:
            cleanup_media(media_info)
