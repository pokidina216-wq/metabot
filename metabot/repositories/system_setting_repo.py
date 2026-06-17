"""
Репозиторий системных настроек.
"""
from __future__ import annotations

from typing import Optional

from metabot.models.system_setting import SystemSetting
from .base import BaseRepository


class SystemSettingRepository(BaseRepository[SystemSetting]):
    model = SystemSetting

    async def get_value(self, key: str, default: str | None = None) -> Optional[str]:
        entry = await self.get_one(key=key)
        if entry:
            return entry.value
        return default

    async def set_value(self, key: str, value: str, description: str | None = None) -> None:
        entry = await self.get_one(key=key)
        if entry:
            entry.value = value
            if description:
                entry.description = description
            await self.session.flush()
        else:
            await self.create(key=key, value=value, description=description)
