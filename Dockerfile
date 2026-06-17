# ── Vexis — Production Dockerfile (multi-stage) ──
# Стейдж 1: сборка зависимостей в venv. Стейдж 2: тонкий runtime.

FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Виртуальное окружение, которое перенесём в runtime
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install -r requirements.txt


# ── Runtime ──────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Системные зависимости (ffprobe для видео-метаданных, libmagic для типов)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        libmagic1 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# venv из builder-стейджа
COPY --from=builder /opt/venv /opt/venv

# Код приложения
COPY . .

# Директория для логов + non-root пользователь
RUN mkdir -p /app/logs && \
    adduser --disabled-password --gecos '' botuser && \
    chown -R botuser:botuser /app
USER botuser

# Health-сервер (polling) слушает на этом порту внутри сети
EXPOSE 8081

# Точка входа: миграции прогоняются отдельным шагом (compose: service migrate,
# Railway: pre-deploy / start.sh). По умолчанию — polling.
CMD ["python", "bot.py"]
