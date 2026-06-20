"""
OSINT по нику (username).

ВАЖНО (audit-fix): источник возвращает СПИСОК РЕАЛЬНО найденных URL.
Никаких «найдено на сайте X» — либо конкретные ссылки на профили, либо
пустой результат. Пользователю должно быть очевидно: либо профиль
действительно существует и проверен HTTP-запросом, либо «ничего не найдено».

Стратегия проверки:
- Каждая платформа описана через PlatformCheck. Для одной части платформ
  достаточно сравнить HTTP-код (404 у несуществующего профиля); для другой —
  нужно искать в теле страницы маркер «не существует»/«существует».
- Используется GET (не HEAD): на многих платформах HEAD кэшируется или
  возвращает один и тот же код для существующего и несуществующего юзера.
- Запросы выполняются параллельно с общим клиентом httpx (connection pool)
  и общим таймаутом. Сетевая/HTTP-ошибка трактуется как «не найдено», а не
  как «найдено» — мы не показываем мусорные совпадения.
- Список платформ — только те, для которых проверка надёжна. Сайты с
  агрессивным анти-ботом (Instagram, Twitter/X, LinkedIn, TikTok, Facebook)
  исключены: они дают false-positive на любой ник.

Валидация ника: только [A-Za-z0-9._-]{2..32}. Так мы не отправляем «грязные»
строки на чужие домены и не получаем нерелевантные совпадения.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

import httpx

from metabot.osint.base_source import BaseOsintSource, OsintFinding

logger = logging.getLogger(__name__)


_USERNAME_RE = re.compile(r"^[A-Za-z0-9._\-]{2,32}$")

# Заголовки максимально близкие к обычному браузеру — чтобы CDN не давали
# нам отбойную страницу с другим статусом.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    "Cache-Control": "no-cache",
}

_REQUEST_TIMEOUT = httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=5.0)
_PARALLEL = 8  # одновременно держим не больше 8 коннектов


@dataclass(frozen=True)
class PlatformCheck:
    """Описание правила проверки одной платформы."""
    name: str
    url_template: str  # содержит "{u}", куда подставляется username

    # Способ проверки: "status" → смотрим HTTP-код; "body" → ищем сигнатуру
    # в теле страницы (используется, когда сервер на 200 всегда отдаёт
    # шаблон, а отличить можно только по содержимому).
    mode: str = "status"

    # Для mode="status": какие коды значат «профиль существует».
    success_codes: Tuple[int, ...] = (200,)
    # Для mode="body": сигнатуры в теле. Если ЛЮБАЯ из not_found_markers
    # встретилась → профиля нет. Иначе если ЛЮБАЯ из exists_markers
    # встретилась → есть. Если ни той, ни другой группы — считаем «нет».
    exists_markers: Tuple[str, ...] = ()
    not_found_markers: Tuple[str, ...] = ()

    def build_url(self, username: str) -> str:
        return self.url_template.format(u=username)


# Курируемый список платформ. Сюда добавлены только те, где проверка по
# статус-коду или сигнатуре в теле даёт стабильный результат на «пустом»
# (без авторизации) запросе и без агрессивного rate-limit.
PLATFORMS: List[PlatformCheck] = [
    PlatformCheck("GitHub", "https://github.com/{u}"),
    PlatformCheck("GitLab", "https://gitlab.com/{u}"),
    PlatformCheck("Bitbucket", "https://bitbucket.org/{u}/"),
    PlatformCheck("Reddit", "https://www.reddit.com/user/{u}/about.json"),
    PlatformCheck("Habr", "https://habr.com/ru/users/{u}/"),
    PlatformCheck("DEV.to", "https://dev.to/{u}"),
    PlatformCheck("Hashnode", "https://hashnode.com/@{u}"),
    PlatformCheck("Medium", "https://medium.com/@{u}"),
    PlatformCheck("Pinterest", "https://www.pinterest.com/{u}/"),
    PlatformCheck("Twitch", "https://www.twitch.tv/{u}"),
    PlatformCheck("YouTube", "https://www.youtube.com/@{u}"),
    PlatformCheck("Keybase", "https://keybase.io/{u}"),
    PlatformCheck("Vimeo", "https://vimeo.com/{u}"),
    PlatformCheck("Behance", "https://www.behance.net/{u}"),
    PlatformCheck("SoundCloud", "https://soundcloud.com/{u}"),
    PlatformCheck("Codeforces", "https://codeforces.com/profile/{u}"),
    PlatformCheck("Pastebin", "https://pastebin.com/u/{u}"),
    PlatformCheck("Replit", "https://replit.com/@{u}"),
    # Платформы, где сайт на 200 отдаёт шаблон в любом случае —
    # проверяем по сигнатуре в теле.
    PlatformCheck(
        "Telegram",
        "https://t.me/{u}",
        mode="body",
        # Несуществующий канал/юзер: t.me отдаёт страницу с этим текстом.
        not_found_markers=(
            "If you have Telegram, you can contact",  # это тоже для несущ.
            "tgme_page_extra",
        ),
        # Существующий: появляется блок с tgme_page_title (имя/название).
        exists_markers=("tgme_page_title",),
    ),
    PlatformCheck(
        "VK",
        "https://vk.com/{u}",
        mode="body",
        not_found_markers=(
            "Страница удалена либо ещё не создана",
            "404 Not Found",
            "page_not_found",
        ),
        exists_markers=('<meta property="og:type" content="profile"',
                        '"page_name"', "profile_short_info"),
    ),
    PlatformCheck(
        "Steam",
        "https://steamcommunity.com/id/{u}",
        mode="body",
        not_found_markers=(
            "The specified profile could not be found",
            "Could not find user",
        ),
        exists_markers=('<title>Steam Community',),
    ),
]


def _is_valid_username(query: str) -> bool:
    return bool(_USERNAME_RE.match(query or ""))


async def _check_one(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    check: PlatformCheck,
    username: str,
) -> Optional[str]:
    """Проверить одну платформу. Вернуть URL если профиль есть, иначе None.

    Любая ошибка трактуется как «не найдено»: мы не вешаем на пользователя
    мусорные совпадения. Логируем только на DEBUG.
    """
    url = check.build_url(username)
    try:
        async with semaphore:
            resp = await client.get(url, headers=_HEADERS)
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:
        logger.debug("username_osint: %s network error for %s: %s",
                     check.name, url, exc)
        return None

    try:
        if check.mode == "status":
            return url if resp.status_code in check.success_codes else None

        if check.mode == "body":
            # 4xx — однозначно нет.
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                return None
            # 5xx — не можем подтвердить; не показываем.
            if resp.status_code >= 500:
                return None
            body = resp.text or ""
            for bad in check.not_found_markers:
                if bad in body:
                    return None
            for good in check.exists_markers:
                if good in body:
                    return url
            # Маркеров нет → не подтверждено.
            return None

        logger.debug("username_osint: unknown mode %r on %s",
                     check.mode, check.name)
        return None
    except Exception as exc:  # noqa: BLE001 — защитная обвязка
        logger.debug("username_osint: %s parse error: %s", check.name, exc)
        return None


class UsernameOsintSource(BaseOsintSource):
    """OSINT-источник: ищет ник по списку известных платформ.

    Возвращает только реально подтверждённые ссылки (см. PlatformCheck).
    Если ничего не нашли — found=False, urls=[]. Хендлер по этому случаю
    показывает «Ничего не найдено» (см. osint_handler.py).
    """

    name = "Username OSINT"
    # Категория совпадает с тем, что использует «По нику» в osint_handler.
    category = "nick"

    def __init__(self, platforms: Optional[Iterable[PlatformCheck]] = None) -> None:
        self._platforms: List[PlatformCheck] = list(platforms) if platforms else PLATFORMS

    async def search(self, query: str) -> OsintFinding:
        username = (query or "").strip().lstrip("@")
        if not _is_valid_username(username):
            return OsintFinding(
                source_name=self.name,
                found=False,
                error="Некорректный ник",
            )

        semaphore = asyncio.Semaphore(_PARALLEL)
        # follow_redirects=True: профили часто редиректят на канонический URL;
        # это нормально и не означает «не найден».
        async with httpx.AsyncClient(
            timeout=_REQUEST_TIMEOUT,
            follow_redirects=True,
            http2=False,
        ) as client:
            tasks = [
                _check_one(client, semaphore, p, username)
                for p in self._platforms
            ]
            results = await asyncio.gather(*tasks, return_exceptions=False)

        urls: List[str] = [u for u in results if u]
        if not urls:
            return OsintFinding(source_name=self.name, found=False)

        return OsintFinding(
            source_name=self.name,
            found=True,
            urls=urls,
            data={"Найдено ссылок": str(len(urls))},
        )
