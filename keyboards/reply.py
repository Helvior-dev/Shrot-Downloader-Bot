from aiogram.types import ReplyKeyboardRemove

def get_main_keyboard() -> ReplyKeyboardRemove:
    """Returns ReplyKeyboardRemove to hide bottom keyboard buttons."""
    return ReplyKeyboardRemove()

