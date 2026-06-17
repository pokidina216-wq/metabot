"""
OSINT по username — проверка присутствия на популярных платформах.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from metabot.osint.base_source import BaseOsintSource, OsintFinding

logger = logging.getLogger(__name__)

# Платформы для проверки (URL, код успешного ответа)
PLATFORMS = [
    ("GitHub", "https://github.com/{}", 200),
    ("Twitter/X", "https://x.com/{}", 200),
    ("Instagram", "https://www.instagram.com/{}/", 200),
    ("Reddit", "https://www.reddit.com/user/{}", 200),
    ("TikTok", "https://www.tiktok.com/@{}", 200),
    ("YouTube", "https://www.youtube.com/@{}", 200),
    ("Telegram", "https://t.me/{}", 200),
    ("VK", "https://vk.com/{}", 200),
    ("Pinterest", "https://www.pinterest.com/{}/", 200),
    ("LinkedIn", "https://www.linkedin.com/in/{}/", 200),
    ("Medium", "https://medium.com/@{}", 200),
    ("Habr", "https://habr.com/ru/users/{}/", 200),
]


class UsernameOsintSource(BaseOsintSource):
    name = "Username OSINT"
    category = "username"

    async def search(self, query: str) -> OsintFinding:
        found_platforms = []

        async with httpx.AsyncClient(
            timeout=10,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Vexis/2.0)"},
        ) as client:
            tasks = [
                self._check_platform(client, name, url.format(query), ok_code)
                for name, url, ok_code in PLATFORMS
            ]
            results = await asyncio.gather(*tasks)

        for platform_name, exists in results:
            if exists:
                found_platforms.append(platform_name)

        if not found_platforms:
            return OsintFinding(source_name=self.name, found=False)

        data = {
            "Найден на": ", ".join(found_platforms),
            "Платформ": str(len(found_platforms)),
        }
        return OsintFinding(source_name=self.name, found=True, data=data)

    @staticmethod
    async def _check_platform(
        client: httpx.AsyncClient, name: str, url: str, ok_code: int
    ) -> tuple[str, bool]:
        try:
            resp = await client.head(url)
            return name, resp.status_code == ok_code
        except Exception:
            return name, False
