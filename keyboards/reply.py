from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def get_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🚀 Start"),
                KeyboardButton(text="ℹ️ Help"),
            ]
        ],
        resize_keyboard=True
    )
