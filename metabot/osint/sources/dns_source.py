"""
DNS-модуль — записи DNS для домена.
"""
from __future__ import annotations

import asyncio
import logging
import socket

from metabot.osint.base_source import BaseOsintSource, OsintFinding

logger = logging.getLogger(__name__)


class DnsSource(BaseOsintSource):
    name = "DNS Lookup"
    category = "domain"

    async def search(self, query: str) -> OsintFinding:
        try:
            loop = asyncio.get_event_loop()

            # A-записи
            try:
                ips = await loop.run_in_executor(
                    None, lambda: socket.getaddrinfo(query, None, socket.AF_INET)
                )
                a_records = list({addr[4][0] for addr in ips})
            except socket.gaierror:
                a_records = []

            # MX-записи (через dig)
            mx_records = []
            try:
                import subprocess
                result = await loop.run_in_executor(
                    None,
                    lambda: subprocess.run(
                        ["dig", "+short", "MX", query],
                        capture_output=True, text=True, timeout=10
                    )
                )
                if result.returncode == 0 and result.stdout.strip():
                    mx_records = result.stdout.strip().split("\n")[:5]
            except Exception:
                pass

            if not a_records and not mx_records:
                return OsintFinding(source_name=self.name, found=False)

            data = {}
            if a_records:
                data["A-записи"] = ", ".join(a_records[:5])
            if mx_records:
                data["MX-записи"] = ", ".join(mx_records[:5])

            return OsintFinding(source_name=self.name, found=True, data=data)

        except Exception as e:
            return OsintFinding(source_name=self.name, error=str(e)[:200])
