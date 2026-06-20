"""
DNS-модуль — записи DNS для домена.

Audit-fix: раньше MX-записи получались через `subprocess.run(["dig", ...])`,
но dig не входит в production-образ (Docker `python:3.12-slim` без
`dnsutils`). На самом деле в зависимостях уже есть `dnspython` — теперь
используем его, без сабпроцесса и зависимости от ОС.
"""
from __future__ import annotations

import asyncio
import logging
import socket
from typing import List

from metabot.osint.base_source import BaseOsintSource, OsintFinding

logger = logging.getLogger(__name__)


def _resolve_sync(query: str) -> tuple[list[str], list[str], list[str]]:
    """Синхронная резолюция A/MX/NS-записей через dnspython.

    Запускается в executor'е, чтобы не блокировать event loop.
    """
    a_records: list[str] = []
    mx_records: list[str] = []
    ns_records: list[str] = []

    # A
    try:
        infos = socket.getaddrinfo(query, None, socket.AF_INET)
        a_records = sorted({addr[4][0] for addr in infos})
    except socket.gaierror:
        pass

    try:
        import dns.resolver  # type: ignore

        resolver = dns.resolver.Resolver()
        resolver.lifetime = 8.0
        resolver.timeout = 4.0

        try:
            mx_answers = resolver.resolve(query, "MX")
            mx_records = [str(r.exchange).rstrip(".") for r in mx_answers][:10]
        except Exception:  # noqa: BLE001
            mx_records = []

        try:
            ns_answers = resolver.resolve(query, "NS")
            ns_records = [str(r.target).rstrip(".") for r in ns_answers][:10]
        except Exception:  # noqa: BLE001
            ns_records = []
    except Exception as e:  # noqa: BLE001
        logger.debug("dnspython unavailable or failed: %s", e)

    return a_records, mx_records, ns_records


class DnsSource(BaseOsintSource):
    name = "DNS Lookup"
    category = "domain"

    async def search(self, query: str) -> OsintFinding:
        try:
            loop = asyncio.get_event_loop()
            a, mx, ns = await loop.run_in_executor(
                None, _resolve_sync, query
            )

            if not a and not mx and not ns:
                return OsintFinding(source_name=self.name, found=False)

            data: dict[str, str] = {}
            if a:
                data["A-записи"] = ", ".join(a[:5])
            if mx:
                data["MX-записи"] = ", ".join(mx[:5])
            if ns:
                data["NS-серверы"] = ", ".join(ns[:5])

            return OsintFinding(source_name=self.name, found=True, data=data)

        except Exception as e:  # noqa: BLE001
            return OsintFinding(source_name=self.name, error=str(e)[:200])
