"""
OSINT-движок — оркестрация запросов по всем источникам.
Поддерживает:
  1. Встроенные модули (Python-классы)
  2. Динамические API-источники из БД (конструктор источников)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Type

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from metabot.models.osint import OsintSource
from metabot.repositories.osint_repo import OsintSourceRepository
from .base_source import BaseOsintSource, OsintFinding

logger = logging.getLogger(__name__)


class DynamicApiSource(BaseOsintSource):
    """
    Источник, созданный через админ-панель.
    Выполняет HTTP-запрос по конфигурации из БД.
    """

    def __init__(self, source: OsintSource) -> None:
        self.name = source.name
        self.category = source.category
        self.config = source.config or {}

    async def search(self, query: str) -> OsintFinding:
        url = self.config.get("url", "")
        method = self.config.get("method", "GET").upper()
        headers = self.config.get("headers", {})
        params_template = self.config.get("params_template", {})
        body_template = self.config.get("body_template", {})
        response_path = self.config.get("response_path", "")
        timeout = self.config.get("timeout", 15)

        if not url:
            return OsintFinding(source_name=self.name, error="URL не задан")

        # Подставляем query
        params = {k: v.replace("{query}", query) for k, v in params_template.items()}
        body = {k: v.replace("{query}", query) if isinstance(v, str) else v
                for k, v in body_template.items()} if body_template else None

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if method == "GET":
                    resp = await client.get(url, params=params, headers=headers)
                else:
                    resp = await client.post(url, json=body, params=params, headers=headers)

                resp.raise_for_status()
                data = resp.json()

                # Навигация по response_path (e.g., "data.results")
                result_data = data
                if response_path:
                    for part in response_path.split("."):
                        if isinstance(result_data, dict):
                            result_data = result_data.get(part, {})
                        else:
                            break

                found = bool(result_data)
                if isinstance(result_data, dict):
                    display = result_data
                elif isinstance(result_data, list):
                    display = {"Результатов": len(result_data)}
                    for i, item in enumerate(result_data[:5]):
                        display[f"#{i+1}"] = str(item)[:200]
                else:
                    display = {"Результат": str(result_data)[:500]}

                return OsintFinding(source_name=self.name, found=found, data=display)

        except httpx.HTTPStatusError as e:
            return OsintFinding(source_name=self.name, error=f"HTTP {e.response.status_code}")
        except Exception as e:
            return OsintFinding(source_name=self.name, error=str(e)[:200])


class OsintEngine:
    """
    Главный OSINT-движок.
    Регистрирует встроенные источники + загружает динамические из БД.
    """

    def __init__(self) -> None:
        self._builtin: Dict[str, List[BaseOsintSource]] = {}

    def register(self, source: BaseOsintSource) -> None:
        """Зарегистрировать встроенный модуль."""
        self._builtin.setdefault(source.category, []).append(source)
        logger.info("OSINT source registered: %s [%s]", source.name, source.category)

    async def search(
        self,
        category: str,
        query: str,
        session: AsyncSession,
        include_premium: bool = False,
    ) -> List[OsintFinding]:
        """
        Запустить поиск по всем источникам категории.
        Запросы выполняются параллельно.
        """
        sources: List[BaseOsintSource] = []

        # Встроенные
        sources.extend(self._builtin.get(category, []))

        # Динамические из БД
        repo = OsintSourceRepository(session)
        db_sources = await repo.get_enabled_by_category(category)
        for s in db_sources:
            if s.is_premium and not include_premium:
                continue
            sources.append(DynamicApiSource(s))

        if not sources:
            return [OsintFinding(source_name="System", error="Нет доступных источников")]

        # Параллельный запуск
        tasks = [self._safe_search(src, query) for src in sources]
        results = await asyncio.gather(*tasks)
        return list(results)

    @staticmethod
    async def _safe_search(source: BaseOsintSource, query: str) -> OsintFinding:
        try:
            return await asyncio.wait_for(source.search(query), timeout=30)
        except asyncio.TimeoutError:
            return OsintFinding(source_name=source.name, error="Таймаут")
        except Exception as e:
            return OsintFinding(source_name=source.name, error=str(e)[:200])
