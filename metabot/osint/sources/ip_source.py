"""
IP-модуль — геолокация и информация об IP-адресе.
"""
from __future__ import annotations

import logging

import httpx

from metabot.osint.base_source import BaseOsintSource, OsintFinding
from metabot.configs import get_settings

logger = logging.getLogger(__name__)


class IpInfoSource(BaseOsintSource):
    name = "IP Info"
    category = "ip"

    async def search(self, query: str) -> OsintFinding:
        try:
            settings = get_settings()
            headers = {}
            url = f"https://ipinfo.io/{query}/json"
            if settings.ipinfo_token:
                headers["Authorization"] = f"Bearer {settings.ipinfo_token}"

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, headers=headers)

            if resp.status_code != 200:
                return OsintFinding(source_name=self.name, error=f"HTTP {resp.status_code}")

            info = resp.json()
            if info.get("bogon"):
                return OsintFinding(
                    source_name=self.name,
                    found=False,
                    data={"info": "Приватный/зарезервированный IP"},
                )

            data = {
                "IP": info.get("ip", query),
                "Город": info.get("city", "N/A"),
                "Регион": info.get("region", "N/A"),
                "Страна": info.get("country", "N/A"),
                "Организация": info.get("org", "N/A"),
                "ASN": info.get("asn", {}).get("asn", "N/A") if isinstance(info.get("asn"), dict) else "N/A",
                "Провайдер": info.get("org", "N/A"),
                "Часовой пояс": info.get("timezone", "N/A"),
                "Координаты": info.get("loc", "N/A"),
            }
            return OsintFinding(source_name=self.name, found=True, data=data)

        except Exception as e:
            return OsintFinding(source_name=self.name, error=str(e)[:200])
