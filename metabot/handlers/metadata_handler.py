"""
Vexis — анализ метаданных.
Пользователь отправляет файл → мгновенный красивый отчёт.
Никаких дополнительных действий.
"""
from __future__ import annotations

import json
import logging
import time

from aiogram import Bot, Router, F
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from metabot.cache import RedisCache, get_redis
from metabot.keyboards import metadata_result_kb, main_menu_kb
from metabot.metadata import MetadataAnalyzer
from metabot.models.user import User
from metabot.repositories.request_log_repo import RequestLogRepository
from metabot.services.user_service import UserService

logger = logging.getLogger(__name__)

router = Router(name="metadata")

analyzer = MetadataAnalyzer()

# Сколько храним последнюю сводку для кнопки «скачать» (сек).
# Намеренно коротко: это те же данные, что юзер уже увидел в чате (PII-гигиена).
_REPORT_TTL = 1800


def _report_key(tg_id: int) -> str:
    return f"meta:report:{tg_id}"


async def _cache_report(tg_id: int, result) -> None:
    """Сохранить последнюю сводку в Redis для последующей выгрузки файлом."""
    try:
        cache = RedisCache(await get_redis())
        await cache.set_json(
            _report_key(tg_id),
            {
                "file_name": result.file_name,
                "txt": result.to_full_report(),
                "json": result.to_dict(),
            },
            ttl=_REPORT_TTL,
        )
    except Exception as e:  # noqa: BLE001 — кэш не критичен для основного ответа
        logger.warning("Failed to cache metadata report: %s", e)


def _safe_stem(file_name: str) -> str:
    """Безопасное имя файла для выгрузки."""
    stem = "".join(c if c.isalnum() or c in "-_." else "_" for c in (file_name or "file"))
    return (stem.rsplit(".", 1)[0] or "metadata")[:64]


@router.callback_query(F.data.in_({"dl:meta:txt", "dl:meta:json"}))
async def handle_download_report(callback: CallbackQuery, bot: Bot) -> None:
    """Выгрузить полную сводку метаданных файлом (TXT или JSON)."""
    tg_id = callback.from_user.id
    fmt = "json" if callback.data.endswith("json") else "txt"
    try:
        cache = RedisCache(await get_redis())
        cached = await cache.get_json(_report_key(tg_id))
    except Exception as e:  # noqa: BLE001
        logger.warning("Report cache read failed: %s", e)
        cached = None

    if not cached:
        await callback.answer(
            "Сводка устарела — отправь файл ещё раз 🙏", show_alert=True
        )
        return

    stem = _safe_stem(cached.get("file_name", "metadata"))
    if fmt == "json":
        payload = json.dumps(cached["json"], ensure_ascii=False, indent=2).encode("utf-8")
        doc = BufferedInputFile(payload, filename=f"{stem}_metadata.json")
        caption = "🧾 Полная сводка метаданных (JSON)"
    else:
        payload = cached["txt"].encode("utf-8")
        doc = BufferedInputFile(payload, filename=f"{stem}_metadata.txt")
        caption = "📥 Полная сводка метаданных"

    await bot.send_document(chat_id=tg_id, document=doc, caption=caption)
    await callback.answer("Готово ✅")


def _format_report(result, elapsed_ms: int, remaining: int) -> str:
    """Форматирование красивого отчёта."""
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━",
        "  📂 <b>ОТЧЁТ О МЕТАДАННЫХ</b>",
        "━━━━━━━━━━━━━━━━━━━━━━\n",
    ]
    # Основная информация из результата
    raw = result.format_text()
    lines.append(raw)
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"⏱ Время анализа: {elapsed_ms} мс")
    lines.append(f"📊 Осталось запросов: {remaining}")
    return "\n".join(lines)


async def _check_limit(db_user: User, session: AsyncSession, message: Message) -> tuple[bool, int]:
    """Проверить лимит, отправить предупреждение если нужно."""
    user_service = UserService(session)
    allowed, remaining = await user_service.check_and_increment(db_user)
    if not allowed:
        await message.answer(
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "  ⚠️ <b>ЛИМИТ ИСЧЕРПАН</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Дневной лимит запросов исчерпан!\n"
            "💎 Обновите тариф в «💎 Подписка».",
            parse_mode="HTML",
            reply_markup=main_menu_kb(),
        )
    return allowed, remaining


@router.message(F.document)
async def handle_document(
    message: Message, bot: Bot, session: AsyncSession, db_user: User,
) -> None:
    """Обработка файла как документа — основной сценарий."""
    allowed, remaining = await _check_limit(db_user, session, message)
    if not allowed:
        return

    doc = message.document
    wait_msg = await message.answer(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  📂 <b>АНАЛИЗ МЕТАДАННЫХ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📄 Файл: <code>{doc.file_name or 'unknown'}</code>\n"
        "⏳ Анализирую...",
        parse_mode="HTML",
    )

    start_time = time.monotonic()

    try:
        file = await bot.get_file(doc.file_id)
        file_data = await bot.download_file(file.file_path)
        data_bytes = file_data.read()

        result = await analyzer.analyze(
            file_data=data_bytes,
            file_name=doc.file_name or "unknown",
            mime_type=doc.mime_type or "application/octet-stream",
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        log_repo = RequestLogRepository(session)
        await log_repo.create(
            user_id=db_user.id,
            action="metadata_analysis",
            details=f"file={doc.file_name} type={result.file_type}",
            file_type=result.file_type,
            processing_time_ms=elapsed_ms,
        )

        await _cache_report(message.from_user.id, result)
        text = _format_report(result, elapsed_ms, remaining)
        await wait_msg.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=metadata_result_kb(),
            disable_web_page_preview=True,
        )

    except Exception as e:
        logger.exception("Metadata handler error: %s", e)
        await wait_msg.edit_text(
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "  ❌ <b>ОШИБКА АНАЛИЗА</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<code>{str(e)[:200]}</code>",
            parse_mode="HTML",
            reply_markup=metadata_result_kb(),
        )


@router.message(F.photo)
async def handle_photo_warning(message: Message) -> None:
    """Предупреждение при отправке как «Фото»."""
    await message.answer(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  ⚠️ <b>ВНИМАНИЕ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Вы отправили как <b>«Фото»</b>.\n"
        "Telegram автоматически удалил все метаданные.\n\n"
        "<b>Как отправить правильно:</b>\n"
        "1️⃣ Нажмите 📎 (скрепка)\n"
        "2️⃣ Выберите <b>«Файл»</b>, не «Фото»\n"
        "3️⃣ Отправьте файл\n\n"
        "✅ Тогда все оригинальные данные сохранятся!",
        parse_mode="HTML",
    )


@router.message(F.video)
async def handle_video(
    message: Message, bot: Bot, session: AsyncSession, db_user: User,
) -> None:
    """Видео — тоже анализируем."""
    allowed, remaining = await _check_limit(db_user, session, message)
    if not allowed:
        return

    video = message.video
    if video.file_size and video.file_size > 20 * 1024 * 1024:
        await message.answer(
            "⚠️ Видео слишком большое (макс. 20 МБ).\n"
            "Отправьте файл поменьше."
        )
        return

    wait_msg = await message.answer(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🎬 <b>АНАЛИЗ ВИДЕО</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "⏳ Анализирую...",
        parse_mode="HTML",
    )
    start_time = time.monotonic()

    try:
        file = await bot.get_file(video.file_id)
        file_data = await bot.download_file(file.file_path)
        data_bytes = file_data.read()

        result = await analyzer.analyze(
            file_data=data_bytes,
            file_name=video.file_name or "video.mp4",
            mime_type=video.mime_type or "video/mp4",
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        log_repo = RequestLogRepository(session)
        await log_repo.create(
            user_id=db_user.id,
            action="metadata_analysis",
            details=f"video file_name={video.file_name}",
            file_type="video",
            processing_time_ms=elapsed_ms,
        )

        await _cache_report(message.from_user.id, result)
        text = _format_report(result, elapsed_ms, remaining)
        await wait_msg.edit_text(
            text, parse_mode="HTML", reply_markup=metadata_result_kb(),
        )

    except Exception as e:
        logger.exception("Video metadata error: %s", e)
        await wait_msg.edit_text(f"❌ Ошибка: {str(e)[:200]}", parse_mode="HTML")


@router.message(F.audio | F.voice)
async def handle_audio(
    message: Message, bot: Bot, session: AsyncSession, db_user: User,
) -> None:
    """Аудио."""
    allowed, remaining = await _check_limit(db_user, session, message)
    if not allowed:
        return

    audio = message.audio or message.voice
    wait_msg = await message.answer(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🎵 <b>АНАЛИЗ АУДИО</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "⏳ Анализирую...",
        parse_mode="HTML",
    )
    start_time = time.monotonic()

    try:
        file = await bot.get_file(audio.file_id)
        file_data = await bot.download_file(file.file_path)
        data_bytes = file_data.read()

        result = await analyzer.analyze(
            file_data=data_bytes,
            file_name=getattr(audio, "file_name", None) or "audio.mp3",
            mime_type=getattr(audio, "mime_type", None) or "audio/mpeg",
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        log_repo = RequestLogRepository(session)
        await log_repo.create(
            user_id=db_user.id, action="metadata_analysis",
            file_type="audio", processing_time_ms=elapsed_ms,
        )

        await _cache_report(message.from_user.id, result)
        text = _format_report(result, elapsed_ms, remaining)
        await wait_msg.edit_text(
            text, parse_mode="HTML", reply_markup=metadata_result_kb(),
        )

    except Exception as e:
        logger.exception("Audio metadata error: %s", e)
        await wait_msg.edit_text(f"❌ Ошибка: {str(e)[:200]}", parse_mode="HTML")
