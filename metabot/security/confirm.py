"""
Токены подтверждения для опасных операций (Этап 2).

Деструктивные действия (массовая рассылка, очистка, удаление) требуют
двухшагового подтверждения: запрос → одноразовый токен с TTL → подтверждение.
Токены хранятся в Redis, привязаны к Telegram ID инициатора и одноразовы.
"""
from __future__ import annotations

import secrets
from typing import Optional

from metabot.cache import get_redis

_PREFIX = "confirm:"
_DEFAULT_TTL = 120  # секунд


async def issue_token(action: str, actor_tg_id: int, ttl: int = _DEFAULT_TTL) -> str:
    """Создать одноразовый токен подтверждения для действия."""
    token = secrets.token_urlsafe(8)
    redis = await get_redis()
    key = f"{_PREFIX}{action}:{actor_tg_id}:{token}"
    await redis.set(key, "1", ex=ttl)
    return token


async def consume_token(action: str, actor_tg_id: int, token: str) -> bool:
    """Проверить и погасить токен. True — токен был валиден (и теперь удалён)."""
    redis = await get_redis()
    key = f"{_PREFIX}{action}:{actor_tg_id}:{token}"
    # атомарно: получить и удалить
    pipe = redis.pipeline()
    pipe.get(key)
    pipe.delete(key)
    got, _ = await pipe.execute()
    return got is not None
