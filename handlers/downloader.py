import os
import re
import time
import logging
import asyncio
from aiogram import Router, F, Bot, html
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

async def send_carousel(message: Message, media_info: MediaInfo, caption: str | None) -> None:
    """Send photo/video albums in Telegram's groups of up to ten items."""
    for offset in range(0, len(media_info.media_paths), 10):
        paths = media_info.media_paths[offset:offset + 10]
        if len(paths) == 1:
            file = FSInputFile(paths[0])
            if os.path.splitext(paths[0])[1].lower() in IMAGE_EXTENSIONS:
                await message.answer_photo(photo=file, caption=caption if offset == 0 else None, parse_mode=ParseMode.HTML)
            else:
                await message.answer_video(video=file, caption=caption if offset == 0 else None, parse_mode=ParseMode.HTML)
            continue
        media_group = []
        for i, path in enumerate(paths):
            options = {"media": FSInputFile(path), "caption": caption if offset == 0 and i == 0 else None,
                       "parse_mode": ParseMode.HTML}
            kind = InputMediaPhoto if os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS else InputMediaVideo
            media_group.append(kind(**options))
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
                logger.warning(f"Failed to send sticker: {e}")
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
            if progress_msg:
                await progress_msg.edit_text(
                    f"<b>Download limit exceeded</b>\n\n{html.quote(str(e))}",
                    parse_mode=ParseMode.HTML
                )
            return
        except DownloadError as e:
            clean_error = re.sub(r'\x1b\[[0-9;]*m', '', str(e))
            if "Unsupported URL" in clean_error:
                clean_error = "Unsupported URL or private video. Make sure the link is correct."
            elif "Unexpected response" in clean_error or "extractor" in clean_error.lower():
                clean_error = "The platform temporarily restricted direct access or the video is private/unavailable. Please try again in a few moments."
            if progress_msg:
                await progress_msg.edit_text(f"Download failed.\n\nDetails: {html.quote(clean_error)}")
            return
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            if progress_msg:
                await progress_msg.edit_text("An unexpected error occurred.")
            return

        if progress_msg:
            try:
                await progress_msg.edit_text("Uploading to Telegram...")
            except Exception:
                pass

        caption_parts = []
        if media_info.title and media_info.title != "No Title":
            caption_parts.append(f"<b>{html.quote(media_info.title[:100])}</b>")
        if media_info.uploader:
            caption_parts.append(f"By: {html.quote(str(media_info.uploader))}")
        caption = "\n".join(caption_parts) if caption_parts else None

        if media_info.media_type == "carousel" and media_info.media_paths:
            await send_carousel(message, media_info, caption)

        elif media_info.media_type == "photo" and media_info.filepath:
            photo_file = FSInputFile(media_info.filepath)
            await message.answer_photo(
                photo=photo_file,
                caption=caption,
                parse_mode=ParseMode.HTML
            )

        elif media_info.media_type == "audio" and media_info.filepath:
            audio_file = FSInputFile(media_info.filepath)
            await message.answer_audio(
                audio=audio_file,
                caption=caption,
                parse_mode=ParseMode.HTML,
                title=media_info.title[:50] if media_info.title else "Audio",
                performer=media_info.uploader or "Downloader"
            )

        elif media_info.filepath:
            video_file = FSInputFile(media_info.filepath)
            await message.answer_video(
                video=video_file,
                caption=caption,
                parse_mode=ParseMode.HTML,
                duration=media_info.duration or 0,
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

        # Send follow-up confirmation
        await message.answer("Done. Send another link to download more media.", reply_markup=get_main_keyboard())

    except Exception as e:
        logger.error(f"Error sending media: {e}", exc_info=True)
        if progress_msg:
            try:
                await progress_msg.edit_text("Could not send the file to Telegram.")
            except Exception:
                pass
    finally:
        active_downloads.discard(user_id)
        if media_info:
            cleanup_media(media_info)
