"""
OSINT-движок — оркестрация запросов по всем источникам.

Поддерживает:
  1. Встроенные модули (Python-классы).
  2. Динамические API-источники из БД (конструктор источников).

Фиксы audit:
- DynamicApiSource: безопасная подстановка `{query}` в параметры (раньше
  падал, если в params_template/body_template было не-string значение).
- DynamicApiSource: извлечение URL из ответа в `OsintFinding.urls`,
  если он есть и валиден — чтобы вверх по стеку (handlers) попадали
  реальные ссылки, а не «обнаружено на сайте».
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List
from urllib.parse import urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from metabot.models.osint import OsintSource
from metabot.repositories.osint_repo import OsintSourceRepository
from .base_source import BaseOsintSource, OsintFinding

logger = logging.getLogger(__name__)


def _render_param(value: Any, query: str) -> Any:
    """Подставить `{query}` в строковые значения, иначе вернуть как есть."""
    if isinstance(value, str):
        return value.replace("{query}", query)
    if isinstance(value, list):
        return [_render_param(v, query) for v in value]
    if isinstance(value, dict):
        return {k: _render_param(v, query) for k, v in value.items()}
    return value


def _looks_like_url(value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 2048:
        return False
    try:
        p = urlparse(value)
    except Exception:  # noqa: BLE001
        return False
    return p.scheme in ("http", "https") and bool(p.netloc)


def _collect_urls(payload: Any, depth: int = 0) -> List[str]:
    """Рекурсивно собрать все строковые значения, похожие на URL."""
    if depth > 4:
        return []
    if _looks_like_url(payload):
        return [payload]  # type: ignore[list-item]
    out: List[str] = []
    if isinstance(payload, dict):
        for v in payload.values():
            out.extend(_collect_urls(v, depth + 1))
    elif isinstance(payload, list):
        for v in payload:
            out.extend(_collect_urls(v, depth + 1))
    # Дедуп без потери порядка
    seen = set()
    deduped: List[str] = []
    for u in out:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped


class DynamicApiSource(BaseOsintSource):
    """Источник, созданный через админ-панель. Выполняет HTTP-запрос
    по конфигурации из БД."""

    def __init__(self, source: OsintSource) -> None:
        self.name = source.name
        self.category = source.category
        self.config: Dict[str, Any] = source.config or {}

    async def search(self, query: str) -> OsintFinding:
        url = self.config.get("url", "")
        method = str(self.config.get("method", "GET")).upper()
        headers = self.config.get("headers", {}) or {}
        params_template = self.config.get("params_template", {}) or {}
        body_template = self.config.get("body_template", {}) or {}
        response_path = self.config.get("response_path", "") or ""
        timeout = float(self.config.get("timeout", 15))

        if not url:
            return OsintFinding(source_name=self.name, error="URL не задан")

        params = _render_param(params_template, query)
        body = _render_param(body_template, query) if body_template else None

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if method == "GET":
                    resp = await client.get(url, params=params, headers=headers)
                else:
                    resp = await client.request(
                        method, url, json=body, params=params, headers=headers,
                    )
                resp.raise_for_status()
                try:
                    data = resp.json()
                except ValueError:
                    return OsintFinding(
                        source_name=self.name,
                        error="Ответ не JSON",
                    )

                # Навигация по response_path (e.g., "data.results")
                result_data: Any = data
                if response_path:
                    for part in response_path.split("."):
                        if isinstance(result_data, dict):
                            result_data = result_data.get(part)
                        else:
                            result_data = None
                            break

                found = bool(result_data)
                urls = _collect_urls(result_data) if found else []

                if isinstance(result_data, dict):
                    display = {k: v for k, v in result_data.items()
                               if not isinstance(v, (dict, list))}
                elif isinstance(result_data, list):
                    display = {"Результатов": len(result_data)}
                    for i, item in enumerate(result_data[:5]):
                        display[f"#{i+1}"] = str(item)[:200]
                elif result_data is None:
                    display = {}
                else:
                    display = {"Результат": str(result_data)[:500]}

                return OsintFinding(
                    source_name=self.name,
                    found=found,
                    data=display,
                    urls=urls,
                )

        except httpx.HTTPStatusError as e:
            return OsintFinding(
                source_name=self.name,
                error=f"HTTP {e.response.status_code}",
            )
        except (httpx.HTTPError, asyncio.TimeoutError) as e:
            return OsintFinding(source_name=self.name, error=type(e).__name__)
        except Exception as e:  # noqa: BLE001
            return OsintFinding(source_name=self.name, error=str(e)[:200])


class OsintEngine:
    """Главный OSINT-движок: регистрирует встроенные источники + подгружает
    динамические из БД."""

    def __init__(self) -> None:
        self._builtin: Dict[str, List[BaseOsintSource]] = {}

    def register(self, source: BaseOsintSource) -> None:
        self._builtin.setdefault(source.category, []).append(source)
        logger.info("OSINT source registered: %s [%s]", source.name, source.category)

    async def search(
        self,
        category: str,
        query: str,
        session: AsyncSession,
        include_premium: bool = False,
    ) -> List[OsintFinding]:
        """Запустить поиск по всем источникам категории. Параллельно.

        Если для категории нет источников — возвращается ПУСТОЙ список.
        Это сознательно: вызывающий код должен показать пользователю
        «Ничего не найдено», а не «нет источников».
        """
        sources: List[BaseOsintSource] = list(self._builtin.get(category, []))

        try:
            repo = OsintSourceRepository(session)
            db_sources = await repo.get_enabled_by_category(category)
            for s in db_sources:
                if s.is_premium and not include_premium:
                    continue
                sources.append(DynamicApiSource(s))
        except Exception as e:  # noqa: BLE001
            # БД-источники не критичны; не валим весь поиск.
            logger.warning("OSINT engine: failed to load DB sources: %s", e)

        if not sources:
            return []

        tasks = [self._safe_search(src, query) for src in sources]
        results = await asyncio.gather(*tasks)
        return list(results)

    @staticmethod
    async def _safe_search(source: BaseOsintSource, query: str) -> OsintFinding:
        try:
            return await asyncio.wait_for(source.search(query), timeout=30)
        except asyncio.TimeoutError:
            return OsintFinding(source_name=source.name, error="Таймаут")
        except Exception as e:  # noqa: BLE001
            return OsintFinding(source_name=source.name, error=str(e)[:200])
