"""
Реферальная система — callback-обработчики.
"""
from __future__ import annotations

from aiogram import Router

router = Router(name="referrals")

# Основная логика рефералов обрабатывается в:
# - handlers/start.py (deep link при /start)
# - handlers/menu.py (кнопка "Рефералы")
# - handlers/payment_handler.py (бонус при покупке)
# Этот роутер зарезервирован для будущих расширений (детальная статистика, промо-коды и т.д.)
