"""
Сервис поиска свободных Telegram Username.
Генерирует и проверяет username через Telegram API (bot.get_chat).
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import random
import string
from dataclasses import dataclass
from enum import Enum
from typing import List

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)


class UsernameFilter(str, Enum):
    LETTERS_ONLY = "letters_only"
    LETTERS_DIGITS = "letters_digits"
    BEAUTIFUL = "beautiful"
    BRAND = "brand"
    REPEATING = "repeating"


@dataclass
class UsernameResult:
    """Результат проверки username."""
    username: str
    available: bool
    filter_type: str


class UsernameService:
    """Генерация и проверка Telegram username."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def find_available(
        self,
        length: int = 5,
        count: int = 10,
        filter_type: UsernameFilter = UsernameFilter.LETTERS_DIGITS,
    ) -> List[UsernameResult]:
        """
        Найти свободные username.
        Генерирует кандидатов и проверяет их доступность.
        """
        length = max(5, min(length, 32))  # Telegram min length = 5
        count = max(1, min(count, 50))

        candidates = self._generate_candidates(length, count * 5, filter_type)
        results: List[UsernameResult] = []

        # Проверяем пачками по 5, чтобы не флудить API
        for batch in self._chunks(candidates, 5):
            tasks = [self._check_username(uname) for uname in batch]
            checked = await asyncio.gather(*tasks)
            for uname, available in checked:
                results.append(
                    UsernameResult(
                        username=uname,
                        available=available,
                        filter_type=filter_type.value,
                    )
                )
                if sum(1 for r in results if r.available) >= count:
                    break
            if sum(1 for r in results if r.available) >= count:
                break
            # Небольшая пауза между пачками
            await asyncio.sleep(0.5)

        # Возвращаем только свободные
        return [r for r in results if r.available][:count]

    async def check_single(self, username: str) -> bool:
        """Проверить конкретный username."""
        _, available = await self._check_username(username)
        return available

    # ── Генерация кандидатов ───────────────────────────────
    def _generate_candidates(
        self, length: int, count: int, filter_type: UsernameFilter
    ) -> List[str]:
        generators = {
            UsernameFilter.LETTERS_ONLY: self._gen_letters,
            UsernameFilter.LETTERS_DIGITS: self._gen_alphanumeric,
            UsernameFilter.BEAUTIFUL: self._gen_beautiful,
            UsernameFilter.BRAND: self._gen_brand,
            UsernameFilter.REPEATING: self._gen_repeating,
        }
        gen = generators.get(filter_type, self._gen_alphanumeric)
        return gen(length, count)

    @staticmethod
    def _gen_letters(length: int, count: int) -> List[str]:
        results = set()
        while len(results) < count:
            uname = "".join(random.choices(string.ascii_lowercase, k=length))
            results.add(uname)
        return list(results)

    @staticmethod
    def _gen_alphanumeric(length: int, count: int) -> List[str]:
        chars = string.ascii_lowercase + string.digits
        results = set()
        while len(results) < count:
            # Первый символ — буква (Telegram requirement)
            uname = random.choice(string.ascii_lowercase) + "".join(
                random.choices(chars, k=length - 1)
            )
            results.add(uname)
        return list(results)

    @staticmethod
    def _gen_beautiful(length: int, count: int) -> List[str]:
        """Красивые — палиндромы и симметричные."""
        results = set()
        vowels = "aeiou"
        consonants = "bcdfghjklmnpqrstvwxyz"
        while len(results) < count:
            half_len = (length + 1) // 2
            half = ""
            for i in range(half_len):
                half += random.choice(consonants if i % 2 == 0 else vowels)
            uname = half + half[:length - half_len][::-1]
            if len(uname) >= 5:
                results.add(uname)
        return list(results)

    @staticmethod
    def _gen_brand(length: int, count: int) -> List[str]:
        """Брендовые — короткие осмысленные комбинации."""
        prefixes = ["go", "my", "in", "on", "up", "hi", "be", "do", "no", "we", "ai", "io"]
        suffixes = ["app", "hub", "lab", "dev", "net", "pro", "bot", "box", "run", "fly"]
        results = set()
        while len(results) < count:
            p = random.choice(prefixes)
            s = random.choice(suffixes)
            middle = "".join(random.choices(string.ascii_lowercase, k=max(0, length - len(p) - len(s))))
            uname = (p + middle + s)[:length]
            if len(uname) >= 5:
                results.add(uname)
        return list(results)

    @staticmethod
    def _gen_repeating(length: int, count: int) -> List[str]:
        """Повторяющиеся символы — aaabb, xxxyz."""
        results = set()
        chars = string.ascii_lowercase
        while len(results) < count:
            base_char = random.choice(chars)
            repeat = random.randint(2, min(4, length - 1))
            rest = "".join(random.choices(chars, k=length - repeat))
            uname = base_char * repeat + rest
            if len(uname) >= 5:
                results.add(uname)
        return list(results)

    # ── Проверка доступности ───────────────────────────────
    async def _check_username(self, username: str) -> tuple[str, bool]:
        """Проверить, свободен ли username через bot.get_chat."""
        try:
            await self.bot.get_chat(f"@{username}")
            return username, False  # Занят
        except TelegramBadRequest:
            return username, True  # Свободен (чат не найден)
        except Exception:
            return username, False  # Ошибка → считаем занятым

    @staticmethod
    def _chunks(lst: list, n: int):
        for i in range(0, len(lst), n):
            yield lst[i : i + n]
