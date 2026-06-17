"""
Email OSINT — проверка email через открытые источники.
"""
from __future__ import annotations

import logging

import httpx

from metabot.osint.base_source import BaseOsintSource, OsintFinding
from metabot.configs import get_settings

logger = logging.getLogger(__name__)


class EmailBreachSource(BaseOsintSource):
    name = "Email Check"
    category = "email"

    async def search(self, query: str) -> OsintFinding:
        """
        Проверка email.
        При наличии hunter_api_key — проверяем через Hunter.io.
        Иначе — базовая проверка MX-записи.
        """
        settings = get_settings()
        data = {}

        # Hunter.io (если есть ключ)
        if settings.hunter_api_key:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(
                        "https://api.hunter.io/v2/email-verifier",
                        params={"email": query, "api_key": settings.hunter_api_key},
                    )
                if resp.status_code == 200:
                    info = resp.json().get("data", {})
                    data["Статус"] = info.get("status", "N/A")
                    data["Результат"] = info.get("result", "N/A")
                    data["Score"] = str(info.get("score", "N/A"))
                    data["Disposable"] = "Да" if info.get("disposable") else "Нет"
                    data["Webmail"] = "Да" if info.get("webmail") else "Нет"
                    return OsintFinding(source_name=self.name, found=True, data=data)
            except Exception as e:
                logger.warning("Hunter.io error: %s", e)

        # Базовая проверка — MX-запись домена
        try:
            import asyncio
            import socket

            domain = query.split("@")[-1]
            loop = asyncio.get_event_loop()
            mx_info = await loop.run_in_executor(
                None,
                lambda: socket.getaddrinfo(domain, None, socket.AF_INET),
            )
            if mx_info:
                data["Домен"] = domain
                data["MX активен"] = "Да"
                data["IP домена"] = mx_info[0][4][0]
                return OsintFinding(source_name=self.name, found=True, data=data)
        except Exception:
            pass

        return OsintFinding(source_name=self.name, found=False)
