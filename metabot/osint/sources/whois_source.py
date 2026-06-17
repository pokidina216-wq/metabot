"""
WHOIS-модуль — информация о домене.
"""
from __future__ import annotations

import asyncio
import logging

from metabot.osint.base_source import BaseOsintSource, OsintFinding

logger = logging.getLogger(__name__)


class WhoisSource(BaseOsintSource):
    name = "WHOIS"
    category = "domain"

    async def search(self, query: str) -> OsintFinding:
        try:
            import whois  # python-whois

            loop = asyncio.get_event_loop()
            w = await loop.run_in_executor(None, whois.whois, query)

            if not w or not w.domain_name:
                return OsintFinding(source_name=self.name, found=False)

            data = {
                "Домен": str(w.domain_name),
                "Регистратор": str(w.registrar or "N/A"),
                "Дата создания": str(w.creation_date or "N/A"),
                "Дата истечения": str(w.expiration_date or "N/A"),
                "Серверы имён": ", ".join(w.name_servers) if w.name_servers else "N/A",
                "Статус": (
                    ", ".join(w.status[:3]) if isinstance(w.status, list) else str(w.status or "N/A")
                ),
                "Страна": str(w.country or "N/A"),
                "Организация": str(w.org or "N/A"),
            }
            return OsintFinding(source_name=self.name, found=True, data=data)

        except Exception as e:
            return OsintFinding(source_name=self.name, error=str(e)[:200])
