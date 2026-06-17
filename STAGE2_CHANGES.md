# Vexis — Этап 2 (Безопасность: RBAC + защита Owner + аудит) + Stage 4 (атомные лимиты, тесты, CI, бэкап)

Документ описывает изменения поверх Этапа 0 (безопасность инфраструктуры) и
Этапа 1 (ручная монетизация). Всё проверено на живой PostgreSQL; миграции
проходят upgrade/downgrade без дрейфа схемы. Набор тестов — 20 шт., все зелёные.

---

## 1. Модель ролей (RBAC)

**`metabot/models/user.py`**
- В enum `UserRole` добавлено значение **`OWNER = "owner"`** (высший ранг).
- `PREMIUM` и `SUPERADMIN` помечены как legacy/совместимость.
- Новая колонка **`users.is_protected`** (Boolean, default `false`) — флаг защиты записи.

**`metabot/security/roles.py`** (новый)
- `Permission` — атомарные права (`USE_BOT`, `BAN_USER`, `BROADCAST`,
  `APPROVE_SUBSCRIPTION`, `MANAGE_ROLES`, `DESTRUCTIVE_OPS`, …).
- `ROLE_RANK` + `ROLE_PERMISSIONS` — матрица с наследованием по рангу
  (USER < MODERATOR < ADMIN < OWNER). Роль получает все права ролей ниже.
- `effective_role(db_role, telegram_id, owner_id)` — **владелец определяется по
  `telegram_id`, а не по полю в БД**. Испорченное поле `role` не понизит Owner.
- `is_owner()`, `has_permission()`, `require_permission()` (бросает `PermissionDenied`).
- legacy: `SUPERADMIN` ≈ `ADMIN` (без owner-прав), `PREMIUM` ≈ `USER`.

**`metabot/middlewares/rbac.py`** (новый) — `RBACMiddleware` кладёт в `data`
вычисленные `effective_role` и `is_owner` (после Auth). Зарегистрирован в `bot.py`.

---

## 2. Защита Owner (defense in depth — код + БД)

**`metabot/services/security_service.py`** (новый) — `SecurityAuditService`:
- `ensure_owner_protected(owner_id)` — идемпотентно помечает запись владельца
  `is_protected=True`, роль `OWNER`, снимает бан. Вызывается:
  - при старте бота (`ensure_owner_protected_on_start` в `bot.py`);
  - при первом обращении владельца (`AuthMiddleware`, чтобы защита включалась
    сразу, а не только после рестарта).
- `assert_not_owner_target(target, owner_id, op, actor_tg_id)` — **код-уровневая**
  проверка перед деструктивной операцией; бросает `OwnerProtectionError` и пишет
  `security_event` (severity=critical).

**Триггеры БД** (миграция, см. ниже) — последний рубеж, работает даже при прямом
SQL в обход кода:
- `vexis_protect_owner()` на `users` (BEFORE UPDATE/DELETE): для строк с
  `is_protected=true` запрещает бан (`is_banned=true`), смену роли, снятие защиты
  и удаление. Поднимает исключение `owner_protected: …`.
- `vexis_append_only()` на `audit_log` и `security_events` (BEFORE UPDATE/DELETE):
  любые UPDATE/DELETE запрещены → журналы неизменяемы (append-only).

---

## 3. Аудит

**`metabot/models/audit.py`** (новый)
- `AuditLog` (таблица `audit_log`): actor (user_id/tg_id/role), `action`, target
  (user_id/tg_id/type/id), `before`/`after` (JSONB), `note`, `created_at`.
  Неизменяемая (append-only через триггер).
- `SecurityEvent` (таблица `security_events`): `event_type`, `severity`,
  `actor_tg_id`, `detail` (JSONB). Тоже append-only.

**Интеграция:**
- `admin_handler` — бан пользователя: сначала `assert_not_owner_target`
  (отказ с понятным сообщением «нельзя забанить владельца»), затем запись в
  `audit_log` (before/after) + legacy `admin_actions`. `is_admin()` теперь
  всегда `True` для владельца; иконка роли 👑.
- `payment_handler` — апрув/реджект подписки пишет запись в `audit_log`
  (`subscription_approved` / `subscription_rejected`).

---

## 4. Токены подтверждения опасных операций

**`metabot/security/confirm.py`** (новый) — одноразовые TTL-токены в Redis
(`issue_token` / `consume_token`, атомарное гашение через pipeline). Готово к
использованию для массовой рассылки/очистки (двухшаговое подтверждение Owner).

---

## 5. Атомарные дневные лимиты (Stage 4 — устранение гонки)

Старый `UserService.check_and_increment` читал счётчик, сравнивал с лимитом и
потом инкрементировал — классический TOCTOU: два параллельных запроса могли
оба пройти проверку и превысить лимит.

**`metabot/repositories/user_repo.py`** — новый `try_consume_daily(user_id, limit)`:
- идемпотентный дневной сброс (условный UPDATE по дате);
- **условный атомарный** `UPDATE … WHERE daily_requests_used < limit … RETURNING`
  — инкремент происходит только если лимит не исчерпан. Возвращает
  `(allowed, remaining)`.

`UserService.check_and_increment` переписан поверх этого метода. Тест на 30
параллельных списаний подтверждает: ровно `limit` успешных, переполнения нет.

---

## 6. Миграция

`alembic/versions/9b2c4e1f0a77_stage2_security.py`
(down_revision `802d3d950060`):
- `ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'owner'` (идемпотентно, PG 12+);
- `users.is_protected`;
- таблицы `audit_log`, `security_events` + индексы;
- функции и триггеры `vexis_protect_owner` / `vexis_append_only`.
- **downgrade**: дропает всё, КРОМЕ значения enum `'owner'` — PostgreSQL не
  поддерживает удаление значений enum без пересоздания типа (задокументировано).

Цепочка миграций линейна, один head:
`baseline → subscription_requests → stage2_security`.

---

## 7. Тесты, CI, бэкап (production-grade)

- **`tests/`** — pytest-набор на эфемерной PostgreSQL (pgserver), 20 тестов:
  - `test_rbac.py` — матрица прав, override Owner по telegram_id, legacy-роли;
  - `test_owner_protection.py` — триггеры БД (бан/смена роли/удаление/снятие
    защиты заблокированы), обычный юзер банится, код-гард, append-only аудит;
  - `test_limits.py` — лимит соблюдается, **нет переполнения при 30 параллельных
    списаниях**, free-лимит;
  - `test_subscriptions.py` — заявка → анти-дубль → апрув → идемпотентность → реджект.
- **`.github/workflows/ci.yml`** — ruff + pytest на каждый push/PR.
- **`requirements-dev.txt`** — dev/test-зависимости отдельно от продакшен-образа.
- **`scripts/backup.sh`** — `pg_dump | gzip` с ротацией (14 последних). Готов к
  запуску как Railway cron / scheduled service.

---

## Что осталось (честно)

- **Этап 3** (Telegram-поиск / OSINT-каталог): реализуется отдельно. Важное
  ограничение, о котором предупреждал: **точную дату регистрации Telegram отдаёт
  не API** — возможна только аппроксимация по диапазону ID. Каталожный поиск
  (catalog-first) и резолвер юзернеймов — следующий шаг.
- Деплой на Railway: инфраструктуру (проект + Postgres + Redis + переменные)
  поднимаю по твоим подтверждениям, но финальный запуск требует кода из GitHub —
  жду доступ к репозиторию. `BOT_TOKEN` ты вводишь сам в Railway Variables.
