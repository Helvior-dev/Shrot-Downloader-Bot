import os
import re
import time
import logging
import asyncio
from aiogram import Router, F, Bot, html
from aiogram.enums import ParseMode, ChatAction
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, FSInputFile

from downloader import download_media, cleanup_media, FileTooLargeError, DownloadError, MediaInfo
from keyboards.reply import get_main_keyboard
from keyboards.inline import get_format_keyboard
from utils.auth import is_user_allowed

logger = logging.getLogger(__name__)

router = Router()

URL_REGEX = re.compile(
    r'https?://(?:www\.)?(?:'
    r'tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com|'
    r'instagram\.com/(?:reel|reels|p|tv)|'
    r'youtube\.com/shorts|youtu\.be|'
    r'pinterest\.com|pin\.it|'
    r'x\.com|twitter\.com'
    r')/[^\s]+',
    re.IGNORECASE
)

# In-memory dictionary for storing user download selections with timestamp for TTL
# Format: {session_id: {"url": url, "time": timestamp}}
url_sessions = {}

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

    user_id = message.from_user.id
    if user_id in active_downloads:
        await message.answer("⏳ Please wait until the current download finishes!")
        return

    text = message.text.strip()
    match = URL_REGEX.search(text)
    if not match:
        if text.startswith("http://") or text.startswith("https://"):
            url = text
        else:
            await message.answer(
                "Send me a link to TikTok, Instagram, Pinterest, or Shorts 😉",
                reply_markup=get_main_keyboard()
            )
            return
    else:
        url = match.group(0)

    # Save URL to session map with TTL timestamp
    session_id = f"s_{message.message_id}_{message.from_user.id}"
    url_sessions[session_id] = {"url": url, "time": time.time()}

    await message.answer("Choose format to download:", reply_markup=get_format_keyboard(session_id))

@router.callback_query(F.data.startswith("fmt:"))
async def process_format_callback(callback: CallbackQuery, bot: Bot) -> None:
    user_id = callback.from_user.id
    if user_id in active_downloads:
        await callback.answer("⏳ Please wait until the current download finishes!", show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Invalid callback data.", show_alert=True)
        return

    fmt_type = parts[1]  # "video", "photo", or "audio"
    session_id = parts[2]
    
    session_data = url_sessions.get(session_id)
    if not session_data:
        await callback.answer("⏳ Session expired. Please send the link again.", show_alert=True)
        await callback.message.delete()
        return
        
    url = session_data["url"]
    active_downloads.add(user_id)

    status_label = "audio" if fmt_type == "audio" else ("photo" if fmt_type == "photo" else "video")
    
    progress_msg = None
    sticker_msg = None
    sticker_id = os.getenv("LOADING_STICKER_ID", "").strip()
    
    try:
        await callback.message.delete()
        
        if sticker_id:
            try:
                sticker_msg = await callback.message.answer_sticker(sticker=sticker_id)
                progress_msg = await callback.message.answer(f"⏳ Downloading {status_label}...")
            except Exception as e:
                logger.warning(f"Failed to send sticker: {e}")
                progress_msg = await callback.message.answer(f"⏳ Downloading {status_label}...")
        else:
            progress_msg = await callback.message.answer(f"⏳ Downloading {status_label}...")
            
        action = ChatAction.RECORD_VOICE if fmt_type == "audio" else (ChatAction.UPLOAD_PHOTO if fmt_type == "photo" else ChatAction.UPLOAD_VIDEO)
        await bot.send_chat_action(chat_id=callback.message.chat.id, action=action)

        last_update_time = time.time()
        
        async def update_progress(percent_str: str):
            nonlocal last_update_time
            now = time.time()
            # Update at most once every 3.5 seconds to avoid FloodWait
            if now - last_update_time > 3.5:
                try:
                    await progress_msg.edit_text(f"📥 Downloading {status_label}... ({percent_str})\n[████████░░░░░░░░░░]")
                    last_update_time = now
                except Exception:
                    pass

        try:
            media_info = await download_media(url, format_type=fmt_type, progress_callback=update_progress)
        except FileTooLargeError:
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
            await progress_msg.edit_text(f"❌ Download failed.\n\nDetails: {html.quote(clean_error)}")
            return
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            await progress_msg.edit_text("❌ An unexpected error occurred.")
            return

        await progress_msg.edit_text("⬆️ Uploading to Telegram...")
        
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
            await callback.message.answer_media_group(media=media_group)

        elif media_info.media_type == "photo" and media_info.filepath:
            photo_file = FSInputFile(media_info.filepath)
            await callback.message.answer_photo(
                photo=photo_file,
                caption=caption,
                parse_mode=ParseMode.HTML
            )

        elif media_info.media_type == "audio":
            audio_file = FSInputFile(media_info.filepath)
            await callback.message.answer_audio(
                audio=audio_file,
                caption=caption,
                parse_mode=ParseMode.HTML,
                title=media_info.title[:50],
                performer=media_info.uploader or "Downloader"
            )

        else:
            video_file = FSInputFile(media_info.filepath)
            await callback.message.answer_video(
                video=video_file,
                caption=caption,
                parse_mode=ParseMode.HTML,
                duration=media_info.duration or 0,
                width=media_info.width or 0,
                height=media_info.height or 0,
                supports_streaming=True
            )

        # Cleanup UI
        if sticker_msg:
            await sticker_msg.delete()
        await progress_msg.delete()

        # Send follow-up message
        await callback.message.answer("✅ Done! Send the next link 🚀", reply_markup=get_main_keyboard())

    except Exception as e:
        logger.error(f"Error sending media: {e}", exc_info=True)
        if progress_msg:
            await progress_msg.edit_text("❌ Error sending file to Telegram.")
    finally:
        # Remove from active downloads
        active_downloads.discard(user_id)
        # Cleanup session
        url_sessions.pop(session_id, None)
        if 'media_info' in locals() and media_info:
            cleanup_media(media_info)
