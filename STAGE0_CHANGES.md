# Vexis — Stage 0: Стабилизация и безопасность (выполнено)

Все изменения проверены на реальном PostgreSQL (миграции, enum, идемпотентность)
и полным smoke-тестом (импорт всех модулей + сборка Dispatcher на aiogram 3.21).

## 0.1 Сеть и пароли (`docker-compose.yml`, `.env.example`, `nginx/nginx.conf`)
- Postgres и Redis больше **не публикуются наружу** (`expose` вместо `ports`).
- Redis запускается с **обязательным паролем** (`--requirepass`), healthcheck с auth.
- Убраны слабые дефолтные пароли — переменные обязательны (`:?` в compose).
- Контейнер бота: `cap_drop: ALL`, `no-new-privileges`, healthcheck через `/health`.
- `migrate` теперь честно делает `alembic upgrade head` (без фолбэка на schema.sql).
- Синхронизированы порты webhook: nginx `bot:8080` + path `/webhook` (было 8443//webhook/).

## 0.2 Секреты (`.gitignore`, удаление файлов)
- Удалён `proxies.txt` (потенциальная утечка прокси) и `bot_lite.py` (тянул его).
- `.gitignore` расширен: `.env.*`, `proxies.txt`, `*.pem`, `*.key`, ssl-ключи.
- ⚠️ Рекомендация: **ротировать** bot token и пароли БД, если архив куда-то уходил.

## 0.3 Одна кодовая база
- Удалён монолитный `bot_lite.py` (834 строки). Канон — пакет `metabot/` + `bot.py`.
- `Dockerfile` переписан: **multi-stage** (venv в builder → тонкий runtime),
  non-root, `EXPOSE 8081` для health-проб.

## 0.4 Единый источник схемы — Alembic
- Сгенерирована и **проверена** baseline-миграция `alembic/versions/1a153d85b1dc_baseline_schema.py`.
- Удалён `sql/schema.sql` (источник истины теперь только Alembic).
- **Найден и исправлен баг enum:** `Enum(UserRole)`/`Enum(PaymentStatus)` хранили бы
  имена членов (UPPERCASE), а `server_default`/legacy-данные — значения (lowercase).
  Добавлены `values_callable` + явные имена типов `user_role` / `payment_status`.
- Миграция управляет ENUM-типами явно (идемпотентный `create checkfirst`,
  `DROP TYPE` в downgrade) — upgrade/downgrade/re-upgrade проходят без ошибок,
  drift между моделями и миграцией = 0.

## 0.5 Логи и health
- `utils/logging_config.py` переписан на **structlog**: JSON в проде (`LOG_FORMAT=json`),
  читаемый console в dev, **маскирование секретов** (token, creds в DB/Redis URL).
- Добавлен `metabot/health.py`: `GET /health` (liveness) и `GET /ready`
  (readiness — проверка Postgres+Redis). Подключён в polling (отдельный порт 8081)
  и webhook (на основном aiohttp-приложении).

## Доп. стабилизация (блокеры, найденные по ходу)
- **Конфликт зависимостей:** `aiogram==3.15` требует `pydantic<2.10`, а был
  закреплён `pydantic==2.10.4` → проект не устанавливался. Поднято до `aiogram==3.21.0`.
- Удалён устаревший пакет `aioredis` (код использует `redis.asyncio`).

## Railway-готовность
- `settings._to_asyncpg`: `DATABASE_URL` вида `postgres://`/`postgresql://` от Railway
  нормализуется в `postgresql+asyncpg://`, вырезается несовместимый `sslmode`.
- Добавлен `OWNER_ID` в конфиг (Telegram ID владельца, для защиты Owner на Этапе 2).
- `scripts/start.sh`: `alembic upgrade head` → `python bot.py`.
- `railway.json`: сборка по Dockerfile, startCommand = `sh scripts/start.sh`.
- README дополнен разделами Railway / миграции / безопасность.

## Доп: фича метаданных (по запросу)
- GPS из EXIF теперь даёт **3 ссылки на карту**: Google Maps, Яндекс.Карты,
  OpenStreetMap (+ высота, если есть в EXIF).
- Кнопки **«📥 Скачать сводку (TXT)»** и **«🧾 Скачать JSON»** под отчётом —
  присылают полную сводку файлом (TXT с картами + структурированный JSON).
- Реализация: `MetadataResult.to_full_report()` / `to_dict()` / `_map_links()`;
  последняя сводка кэшируется в Redis на 30 мин (TTL, PII-гигиена — это те же
  данные, что уже показаны в чате) и отдаётся по callback `dl:meta:txt|json`.

## Что НЕ менялось (намеренно — это следующие этапы)
- Бизнес-логика подписок/платежей (Этап 1), RBAC и защита Owner в коде/триггерах
  (Этап 2), поиск/OSINT (Этап 3). Baseline фиксирует текущую схему как есть,
  чтобы дельты этапов 1–2 накатывались инкрементально.
