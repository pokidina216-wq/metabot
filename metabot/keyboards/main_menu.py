"""
Vexis — Reply-клавиатуры.
Главное меню, навигация, подменю.
"""
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


def main_menu_kb() -> ReplyKeyboardMarkup:
    """Главное меню — 8 кнопок, удобно для одной руки."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🔍 Проверка данных"),
                KeyboardButton(text="📂 Метаданные"),
            ],
            [
                KeyboardButton(text="🔤 Username Finder"),
                KeyboardButton(text="👤 Профиль"),
            ],
            [
                KeyboardButton(text="💎 Подписка"),
                KeyboardButton(text="👥 Рефералы"),
            ],
            [
                KeyboardButton(text="⚙️ Настройки"),
                KeyboardButton(text="❓ Помощь"),
            ],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие 👇",
    )


def metadata_waiting_kb() -> ReplyKeyboardMarkup:
    """Клавиатура ожидания файла для метаданных."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏠 На главную")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Отправьте файл как документ 📎",
    )


def back_to_main_kb() -> ReplyKeyboardMarkup:
    """Кнопка возврата в главное меню."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🏠 На главную")]],
        resize_keyboard=True,
    )
