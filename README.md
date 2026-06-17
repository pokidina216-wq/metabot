# 🔮 Vexis

**Коммерческий SaaS Telegram-бот** для анализа данных, метаданных и цифровой разведки (OSINT).

Production-ready, масштабируемый на 50 000+ пользователей.

## Стек

- Python 3.12+ / Aiogram 3.x
- PostgreSQL + SQLAlchemy Async + Alembic
- Redis (кэш, FSM, rate limiting)
- Docker + Docker Compose
- APScheduler, Pydantic Settings

## Быстрый старт (локально / VPS)

```bash
# 1. Клонировать
git clone <repo> && cd vexis

# 2. Настроить секреты (СИЛЬНЫЕ пароли!)
cp .env.example .env
nano .env    # BOT_TOKEN, OWNER_ID, POSTGRES_PASSWORD, REDIS_PASSWORD

# 3. Запустить
docker compose up -d
```

Compose сам поднимет PostgreSQL и Redis (закрыты от внешней сети, Redis под
паролем), прогонит миграции Alembic (сервис `migrate`), затем стартует бота.

```bash
docker compose logs -f bot     # логи (JSON, structlog)
```

## Деплой на Railway

1. Создай проект и добавь плагины **PostgreSQL** и **Redis** — Railway сам
   выставит переменные `DATABASE_URL` и `REDIS_URL`.
2. Подключи репозиторий. Сборка идёт по `Dockerfile` (см. `railway.json`).
3. Задай переменные окружения сервиса: `BOT_TOKEN`, `OWNER_ID`,
   `LOG_FORMAT=json` (по необходимости остальные из `.env.example`).
4. Деплой. `scripts/start.sh` сам накатит миграции (`alembic upgrade head`)
   и запустит бота в режиме polling — публичный порт не нужен.

> `DATABASE_URL` от Railway автоматически нормализуется в `asyncpg`-драйвер
> (см. `settings._to_asyncpg`), `sslmode` из URL вырезается.

## Возможности

| Раздел | Описание |
|--------|----------|
| 🔍 Проверка данных | OSINT: телефон, email, домен, IP, ник, ФИО, лицо |
| 📂 Метаданные | EXIF/GPS из фото, видео, аудио, PDF, DOCX, XLSX, PPTX, архивов |
| 🔤 Username Finder | Поиск свободных @username с фильтрами |
| 👤 Профиль | Статистика, история запросов |
| 💎 Подписка | Free / Premium / VIP, Stars + карты |
| 👥 Рефералы | Реферальные ссылки, многоуровневая статистика |
| ⚙️ Настройки | Уведомления, язык |
| 🛠 Админ-панель | Аналитика, пользователи, бан, рассылка, OSINT-конструктор |

## UX-принципы

- **Максимум действий через кнопки** — пользователь не вводит команды
- **2-3 клика до результата** — минимум шагов
- **Навигация**: кнопки «← Назад» и «🏠 На главную» везде
- **Пошаговые мастера** (Username Finder: длина → количество → тип → результат)
- **Красивые отчёты** с заголовками и структурой

## Тарифы

| План | Цена | Лимит | OSINT |
|------|------|-------|-------|
| 🆓 Free | $0 | 5/день | ❌ |
| 💎 Premium | $3 / 150⭐ | 50/день | ✅ |
| 👑 VIP | $7 / 350⭐ | 200/день | ✅ |

## Структура проекта

```
vexis/
├── bot.py                  # Точка входа
├── metabot/
│   ├── configs/            # Pydantic Settings
│   ├── models/             # 15 таблиц SQLAlchemy
│   ├── repositories/       # 12 репозиториев (CRUD)
│   ├── services/           # Бизнес-логика
│   ├── handlers/           # 12 роутеров Aiogram
│   ├── keyboards/          # Reply + Inline клавиатуры
│   ├── middlewares/         # DB, Auth, Throttle, Ban, Log
│   ├── metadata/           # Анализатор метаданных
│   ├── osint/              # OSINT-движок + 5 источников
│   ├── cache/              # Redis
│   ├── scheduler/          # APScheduler задачи
│   └── utils/              # Хелперы, логирование
│   └── health.py           # /health, /ready пробы
├── alembic/                # Миграции (единственный источник схемы БД)
├── scripts/start.sh        # migrate → run (Railway / self-host)
├── docker-compose.yml
├── Dockerfile              # multi-stage, non-root
├── railway.json            # конфиг деплоя Railway
├── nginx/nginx.conf        # только для webhook-режима
└── .env.example
```

## Миграции БД

Единственный источник истины — **Alembic** (файл `sql/schema.sql` удалён).

```bash
alembic upgrade head                       # накатить
alembic revision --autogenerate -m "msg"   # сгенерировать новую
alembic downgrade -1                        # откатить на шаг
```

> Для уже существующей БД, созданной старым `schema.sql`, перед первым
> запуском сделай `alembic stamp head`, чтобы зафиксировать baseline без
> повторного создания таблиц.

## Безопасность (Stage 0)

- Postgres/Redis не публикуются наружу; Redis обязательно под паролем.
- Секреты (`.env`, `proxies.txt`, ключи) — только в `.gitignore`, не в образ.
  **Ротируй токены/пароли, если они когда-либо попадали в архив/репозиторий.**
- Логи структурированные (JSON) с маскированием секретов.
- Контейнер non-root, `cap_drop: ALL`, `no-new-privileges`, healthcheck.

## Лицензия

Proprietary © Vexis
