from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def get_format_keyboard(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎬 Video", callback_data=f"fmt:video:{session_id}"),
                InlineKeyboardButton(text="🖼️ Photo", callback_data=f"fmt:photo:{session_id}"),
                InlineKeyboardButton(text="🎵 Audio (MP3)", callback_data=f"fmt:audio:{session_id}")
            ]
        ]
    )
