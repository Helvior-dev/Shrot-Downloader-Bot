import logging
from aiogram import Router, F, html
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message

from keyboards.reply import get_main_keyboard
from utils.auth import is_user_allowed, is_user_admin

logger = logging.getLogger(__name__)

router = Router()

@router.message(CommandStart())
@router.message(F.text == "🚀 Start")
async def command_start_handler(message: Message) -> None:
    user = message.from_user
    logger.info(f"Start command from {user.username} (ID: {user.id})")
    if not is_user_allowed(user):
        logger.warning(f"Access denied for user {user.username} (ID: {user.id})")
        await message.answer(
            f"🔒 <b>Private Bot Mode</b>\n\n"
            f"Your Telegram Username: @{user.username or 'none'}\n"
            f"Your Telegram ID: <code>{user.id}</code>\n\n"
            f"Access denied. Please contact admin.",
            parse_mode=ParseMode.HTML
        )
        return

    user_name = html.bold(user.first_name)
    welcome_text = f"Hi, {user_name}! 👋\n\nSend me a link to download video, photo, or MP3!"
    await message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode=ParseMode.HTML)

@router.message(Command("help"))
@router.message(F.text == "ℹ️ Help")
async def command_help_handler(message: Message) -> None:
    user = message.from_user
    logger.info(f"Help command from {user.username} (ID: {user.id})")
    if not is_user_allowed(user):
        return

    help_text = (
        "ℹ️ <b>Supported Services & Formats:</b>\n\n"
        "🌐 <b>Supported Platforms:</b>\n"
        "• <b>TikTok</b> (Videos & Photo Carousels)\n"
        "• <b>Instagram</b> (Reels, Videos, Photos & Carousels)\n"
        "• <b>YouTube Shorts</b> (Videos & Audio MP3)\n"
        "• <b>Pinterest</b> (Photos & Videos)\n"
        "• <b>Twitter / X</b> (Videos & Photos)\n\n"
        "📁 <b>Available Formats:</b>\n"
        "• 🎬 <b>Video</b> — High-quality video download\n"
        "• 🖼️ <b>Photo</b> — HD Photos & Photo Albums\n"
        "• 🎵 <b>Audio (MP3)</b> — Audio track extraction\n\n"
        "Just send any link to get started 🚀"
    )
    await message.answer(help_text, reply_markup=get_main_keyboard(), parse_mode=ParseMode.HTML)


