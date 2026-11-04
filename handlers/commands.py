import logging
from aiogram import Router, F, html
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message

from keyboards.reply import get_main_keyboard
from downloader import MAX_FILE_SIZE, MAX_PHOTO_SIZE
from utils.auth import is_user_allowed

logger = logging.getLogger(__name__)

router = Router()

@router.message(CommandStart())
@router.message(F.text == "Start")
async def command_start_handler(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    logger.info(f"Start command from {user.username} (ID: {user.id})")
    if not is_user_allowed(user):
        logger.warning(f"Access denied for user {user.username} (ID: {user.id})")
        await message.answer(
            f"<b>Access denied</b>\n\n"
            f"Your Telegram ID: <code>{user.id}</code>\n\n"
            f"Ask the bot owner to add your ID to ALLOWED_USERS.",
            parse_mode=ParseMode.HTML
        )
        return

    user_name = html.bold(user.first_name)
    welcome_text = f"Hi, {user_name}. Send a TikTok, Instagram, Pinterest, or X link to download media."
    await message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode=ParseMode.HTML)

@router.message(Command("help"))
@router.message(F.text == "Help")
async def command_help_handler(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    logger.info(f"Help command from {user.username} (ID: {user.id})")
    if not is_user_allowed(user):
        return

    help_text = (
        "<b>Supported platforms</b>\n"
        "TikTok: videos and photo carousels\n"
        "Instagram: reels, videos, photos and carousels\n"
        "Pinterest: photos and videos\n"
        "X: videos and photos\n\n"
        "Send a link to download the media.\n"
        f"Video/audio limit: {MAX_FILE_SIZE // 1_000_000} MB; photo limit: {MAX_PHOTO_SIZE // 1_000_000} MB."
    )
    await message.answer(help_text, reply_markup=get_main_keyboard(), parse_mode=ParseMode.HTML)
