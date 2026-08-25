import os
import re
import time
import logging
import asyncio
from aiogram import Router, F, Bot, html
from aiogram.enums import ParseMode, ChatAction
from aiogram.types import Message, InputMediaPhoto, FSInputFile

from downloader import download_media, cleanup_media, FileTooLargeError, DownloadError, MediaInfo
from keyboards.reply import get_main_keyboard
from utils.auth import is_user_allowed

logger = logging.getLogger(__name__)

router = Router()

URL_REGEX = re.compile(
    r'https?://(?:[a-zA-Z0-9-]+\.)?(?:'
    r'tiktok\.com|'
    r'instagram\.com/(?:reel|reels|p|tv|share)|'
    r'pinterest\.(?:com|[a-z]{2,3})|pin\.it|'
    r'x\.com|twitter\.com'
    r')/[^\s]+',
    re.IGNORECASE
)

# Anti-spam lock: set of user_ids currently downloading
active_downloads = set()

@router.message(F.text)
async def process_video_link(message: Message, bot: Bot) -> None:
    if not is_user_allowed(message.from_user):
        await message.answer(
            f"🔒 <b>Private Bot Mode</b>\n\n"
            f"Your Telegram ID: <code>{message.from_user.id}</code>\n"
            f"Add this ID to <code>ALLOWED_USERS</code> in <code>.env</code> to gain access.",
            parse_mode=ParseMode.HTML
        )
        return

    text = message.text.strip()

    # Explicitly block YouTube links
    if "youtube.com" in text.lower() or "youtu.be" in text.lower():
        await message.answer(
            "❌ <b>YouTube downloading is disabled</b>\n\n"
            "The bot supports downloading from:\n"
            "• <b>TikTok</b> (Videos & Photo Carousels)\n"
            "• <b>Instagram</b> (Reels, Photos & Carousels)\n"
            "• <b>Pinterest</b> (Photos & Videos)\n"
            "• <b>Twitter / X</b> (Videos & Photos)",
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_keyboard()
        )
        return

    user_id = message.from_user.id
    if user_id in active_downloads:
        await message.answer("⏳ Please wait until the current download finishes!")
        return

    match = URL_REGEX.search(text)
    if not match:
        if text.startswith("http://") or text.startswith("https://"):
            url = text
        else:
            await message.answer(
                "Send me a link to TikTok, Instagram, Pinterest, or X 😉",
                reply_markup=get_main_keyboard()
            )
            return
    else:
        url = match.group(0)

    active_downloads.add(user_id)
    progress_msg = None
    sticker_msg = None
    sticker_id = os.getenv("LOADING_STICKER_ID", "").strip()
    media_info = None

    try:
        if sticker_id:
            try:
                sticker_msg = await message.answer_sticker(sticker=sticker_id)
                progress_msg = await message.answer("⏳ Downloading media...")
            except Exception as e:
                logger.warning(f"Failed to send sticker: {e}")
                progress_msg = await message.answer("⏳ Downloading media...")
        else:
            progress_msg = await message.answer("⏳ Downloading media...")

        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_VIDEO)

        last_update_time = time.time()

        async def update_progress(percent_str: str):
            nonlocal last_update_time
            now = time.time()
            # Update at most once every 3.5 seconds to avoid FloodWait
            if now - last_update_time > 3.5 and progress_msg:
                try:
                    await progress_msg.edit_text(f"📥 Downloading media... ({percent_str})\n[████████░░░░░░░░░░]")
                    last_update_time = now
                except Exception:
                    pass

        try:
            media_info = await download_media(url, format_type="video", progress_callback=update_progress)
        except FileTooLargeError:
            if progress_msg:
                await progress_msg.edit_text(
                    f"⚠️ <b>File Too Large (>50MB)</b>\n\n"
                    f"This video exceeds Telegram's limit.\n"
                    f"<i>Large video compression is in development 🛠️</i>",
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
                await progress_msg.edit_text(f"❌ Download failed.\n\nDetails: {html.quote(clean_error)}")
            return
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            if progress_msg:
                await progress_msg.edit_text("❌ An unexpected error occurred.")
            return

        if progress_msg:
            try:
                await progress_msg.edit_text("⬆️ Uploading to Telegram...")
            except Exception:
                pass

        caption_parts = []
        if media_info.title and media_info.title != "No Title":
            caption_parts.append(f"📹 <b>{html.quote(media_info.title[:100])}</b>")
        if media_info.uploader:
            caption_parts.append(f"👤 {html.quote(str(media_info.uploader))}")
        caption = "\n".join(caption_parts) if caption_parts else None

        if media_info.media_type == "carousel" and media_info.image_paths:
            media_group = [
                InputMediaPhoto(media=FSInputFile(p), caption=caption if i == 0 else None, parse_mode=ParseMode.HTML)
                for i, p in enumerate(media_info.image_paths[:10])
            ]
            await message.answer_media_group(media=media_group)

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
        await message.answer("✅ Done! Send the next link 🚀", reply_markup=get_main_keyboard())

    except Exception as e:
        logger.error(f"Error sending media: {e}", exc_info=True)
        if progress_msg:
            try:
                await progress_msg.edit_text("❌ Error sending file to Telegram.")
            except Exception:
                pass
    finally:
        active_downloads.discard(user_id)
        if media_info:
            cleanup_media(media_info)
