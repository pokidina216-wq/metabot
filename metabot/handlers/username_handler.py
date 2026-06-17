"""
Vexis — Username Finder.
Пошаговый мастер: Длина → Количество → Тип → Результат.
Все шаги через кнопки, минимум ручного ввода.
"""
from __future__ import annotations

import logging

from aiogram import Bot, Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession

from metabot.keyboards import (
    username_length_kb,
    username_count_kb,
    username_type_kb,
    username_result_kb,
    username_check_kb,
)
from metabot.models.user import User
from metabot.repositories.request_log_repo import RequestLogRepository
from metabot.services.user_service import UserService
from metabot.services.username_service import UsernameService, UsernameFilter

logger = logging.getLogger(__name__)

router = Router(name="username")


class UsernameStates(StatesGroup):
    waiting_custom_length = State()
    waiting_check_username = State()


# ═══════════════════════════════════════════════════════════
#  ШАГ 1 — Выбор длины (кнопки)
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("ulen:"))
async def cb_select_length(callback: CallbackQuery, state: FSMContext) -> None:
    """Шаг 1: выбор длины username."""
    value = callback.data.split(":", 1)[1]

    if value == "custom":
        await callback.message.edit_text(
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "  🔤 <b>USERNAME FINDER</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Введите желаемую длину (5–32):",
            parse_mode="HTML",
        )
        await state.set_state(UsernameStates.waiting_custom_length)
        await callback.answer()
        return

    length = int(value)
    await state.update_data(length=length)

    await callback.message.edit_text(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🔤 <b>USERNAME FINDER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ Длина: <b>{length}</b>\n\n"
        "<b>Шаг 2 из 3</b> — Сколько вариантов найти?",
        reply_markup=username_count_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(UsernameStates.waiting_custom_length)
async def process_custom_length(message: Message, state: FSMContext) -> None:
    """Ручной ввод длины."""
    try:
        length = int(message.text.strip())
        if length < 5 or length > 32:
            await message.answer("⚠️ Длина от 5 до 32. Попробуйте снова:")
            return
    except ValueError:
        await message.answer("⚠️ Введите число от 5 до 32:")
        return

    await state.clear()
    await state.update_data(length=length)

    await message.answer(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🔤 <b>USERNAME FINDER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ Длина: <b>{length}</b>\n\n"
        "<b>Шаг 2 из 3</b> — Сколько вариантов найти?",
        reply_markup=username_count_kb(),
        parse_mode="HTML",
    )


# ═══════════════════════════════════════════════════════════
#  ШАГ 2 — Количество (кнопки)
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("ucnt:"))
async def cb_select_count(callback: CallbackQuery, state: FSMContext) -> None:
    """Шаг 2: количество вариантов."""
    count = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    length = data.get("length", 5)
    await state.update_data(count=count)

    await callback.message.edit_text(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🔤 <b>USERNAME FINDER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ Длина: <b>{length}</b>\n"
        f"✅ Количество: <b>{count}</b>\n\n"
        "<b>Шаг 3 из 3</b> — Выберите тип:",
        reply_markup=username_type_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "ustep:back_count")
async def cb_back_to_count(callback: CallbackQuery, state: FSMContext) -> None:
    """Назад к выбору количества."""
    data = await state.get_data()
    length = data.get("length", 5)

    await callback.message.edit_text(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🔤 <b>USERNAME FINDER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ Длина: <b>{length}</b>\n\n"
        "<b>Шаг 2 из 3</b> — Сколько вариантов найти?",
        reply_markup=username_count_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


# ═══════════════════════════════════════════════════════════
#  ШАГ 3 — Тип (кнопки) → ЗАПУСК ПОИСКА
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("utype:"))
async def cb_select_type_and_search(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Шаг 3: выбор типа и запуск поиска."""
    filter_type_str = callback.data.split(":", 1)[1]
    data = await state.get_data()
    length = data.get("length", 5)
    count = data.get("count", 10)
    await state.clear()

    # Проверяем лимиты
    user_service = UserService(session)
    allowed, remaining = await user_service.check_and_increment(db_user)
    if not allowed:
        await callback.message.edit_text(
            "⚠️ <b>Дневной лимит исчерпан!</b>\n\n"
            "💎 Обновите тариф: «💎 Подписка»",
            parse_mode="HTML",
        )
        await callback.answer()
        return

    filter_type = UsernameFilter(filter_type_str)
    type_labels = {
        "letters_only": "🔤 Только буквы",
        "letters_digits": "🔢 Буквы + цифры",
        "beautiful": "✨ Красивые",
        "brand": "🏢 Брендовые",
        "repeating": "🔁 Повторяющиеся",
    }
    type_label = type_labels.get(filter_type_str, filter_type_str)

    await callback.message.edit_text(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🔤 <b>USERNAME FINDER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ Длина: <b>{length}</b>\n"
        f"✅ Количество: <b>{count}</b>\n"
        f"✅ Тип: <b>{type_label}</b>\n\n"
        "⏳ Ищу свободные username...",
        parse_mode="HTML",
    )
    await callback.answer()

    try:
        username_service = UsernameService(bot)
        results = await username_service.find_available(
            length=length, count=count, filter_type=filter_type,
        )

        if not results:
            await callback.message.edit_text(
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "  🔤 <b>USERNAME FINDER</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "😔 Не удалось найти свободные username\n"
                "с заданными параметрами.\n\n"
                "Попробуйте увеличить длину или изменить тип.",
                reply_markup=username_result_kb(),
                parse_mode="HTML",
            )
            return

        lines = [
            "━━━━━━━━━━━━━━━━━━━━━━",
            "  🔤 <b>USERNAME FINDER</b>",
            "━━━━━━━━━━━━━━━━━━━━━━\n",
            f"✅ Найдено <b>{len(results)}</b> свободных username:\n",
        ]
        for i, r in enumerate(results, 1):
            lines.append(f"  {i}. <code>@{r.username}</code>")

        lines.append(f"\n📊 Осталось запросов: {remaining}")

        # Логируем
        log_repo = RequestLogRepository(session)
        await log_repo.create(
            user_id=db_user.id,
            action="username_search",
            details=f"length={length} count={count} filter={filter_type.value} found={len(results)}",
        )

        await callback.message.edit_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=username_result_kb(),
        )

    except Exception as e:
        logger.exception("Username search error: %s", e)
        await callback.message.edit_text(
            "❌ Ошибка поиска. Попробуйте позже.",
            reply_markup=username_result_kb(),
        )


# ═══════════════════════════════════════════════════════════
#  ПРОВЕРКА КОНКРЕТНОГО USERNAME
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "uname:check_one")
async def cb_check_one(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🔍 <b>ПРОВЕРКА USERNAME</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Введите username для проверки (без @):",
        parse_mode="HTML",
    )
    await state.set_state(UsernameStates.waiting_check_username)
    await callback.answer()


@router.message(UsernameStates.waiting_check_username)
async def process_check_one(
    message: Message,
    state: FSMContext,
    bot: Bot,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Проверка конкретного username."""
    username = message.text.strip().lstrip("@")
    await state.clear()

    if len(username) < 5 or len(username) > 32:
        await message.answer(
            "⚠️ Username должен быть от 5 до 32 символов.",
            reply_markup=username_check_kb(),
        )
        return

    user_service = UserService(session)
    allowed, remaining = await user_service.check_and_increment(db_user)
    if not allowed:
        await message.answer(
            "⚠️ <b>Дневной лимит исчерпан!</b>\n💎 Обновите тариф.",
            parse_mode="HTML",
        )
        return

    username_service = UsernameService(bot)
    available = await username_service.check_single(username)

    if available:
        icon, status = "✅", "СВОБОДЕН"
    else:
        icon, status = "❌", "ЗАНЯТ"

    text = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🔍 <b>ПРОВЕРКА USERNAME</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{icon} <code>@{username}</code> — <b>{status}</b>\n\n"
        f"📊 Осталось запросов: {remaining}"
    )

    log_repo = RequestLogRepository(session)
    await log_repo.create(
        user_id=db_user.id,
        action="username_check",
        details=f"username={username} available={available}",
    )

    await message.answer(text, parse_mode="HTML", reply_markup=username_check_kb())
