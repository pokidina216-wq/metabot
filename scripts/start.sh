#!/usr/bin/env sh
# ── Vexis — стартовый скрипт (Railway / self-host) ──
# 1) Накатываем миграции (идемпотентно). 2) Запускаем бота.
# Любая ошибка миграций останавливает старт (set -e).
set -e

echo "[start] running database migrations (alembic upgrade head)..."
alembic upgrade head

echo "[start] launching Vexis bot..."
exec python bot.py "$@"
