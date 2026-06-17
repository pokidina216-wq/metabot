# Этап 1 — Ручная монетизация (manual subscriptions)

Цель: убрать автоплатежи, перевести подписки на модель
**«заявка пользователя → ручное решение Owner»**. Никаких автоматических
выдач, подтверждений оплаты или продлений.

## Что изменилось

### 1. Новая сущность — заявка на подписку
- `metabot/models/subscription.py`:
  - `SubscriptionRequestStatus` (enum: `pending / approved / rejected / cancelled`)
  - `SubscriptionRequest` (таблица `subscription_requests`): `user_id`, `plan_id`,
    `status`, `decided_by` (Telegram ID Owner), `decided_at`, `subscription_id`
    (ссылка на созданную подписку), `note`; индексы по `(user_id, status)` и `status`.
- `metabot/repositories/subscription_repo.py`: `SubscriptionRequestRepository`
  (`get_pending_for_user`, `list_pending`).
- Миграция `alembic/versions/802d3d950060_subscription_requests.py`
  (тип enum создаётся идемпотентным DO-блоком, в таблице — `postgresql.ENUM(create_type=False)`,
  чтобы не было двойного `CREATE TYPE`). Проверено: upgrade/downgrade roundtrip, zero drift.

### 2. Бизнес-логика — `SubscriptionService`
- `create_request(user, plan)` — антидубль: одна `pending`-заявка на пользователя
  (иначе `DuplicateRequestError`).
- `approve_request(request_id, owner_id)` — **идемпотентно**: если уже одобрена —
  no-op; иначе активирует подписку (`payment_method="manual"`) и линкует `subscription_id`.
- `reject_request(request_id, owner_id, note=None)` — идемпотентно.
- `get_pending_request(user_id)`.
- `activate_subscription(...)` сохранён, используется только при ручном одобрении.

### 3. Поток в боте
- `buy:{slug}` (`subscription_handler`): вместо выбора способа оплаты теперь показывает
  карточку тарифа и кнопку **«📨 Оставить заявку»**. Free/бесплатные планы заявку не требуют.
- `req:create:{slug}` (`payment_handler`): создаёт заявку, подтверждает пользователю,
  шлёт **уведомление Owner** (`OWNER_ID`) с кнопками **✅ Одобрить / ❌ Отклонить**.
- `req:approve:{id}` / `req:reject:{id}`: **только Owner** (`from_user.id == OWNER_ID`),
  идемпотентны, уведомляют пользователя о результате.

### 4. Автоплатежи отключены (защитные заглушки)
- `pay:*` → алерт «оплата вручную», направляет на заявку.
- `pre_checkout` → `answer(ok=False)` — платёж не принимается.
- `successful_payment` → **НЕ выдаёт подписку**, логирует и просит написать владельцу
  с ID платежа (контакт `SUPPORT_CONTACT`).

### 5. Настройки
- `SUPPORT_CONTACT` (по умолчанию `VEXIShelper`) — публичный контакт владельца.

## Проверки (всё зелёное)
- Миграция: 17 таблиц, enum `{pending,approved,rejected,cancelled}`, downgrade/​upgrade
  roundtrip, **zero drift**.
- Логика на живой PG: create → антидубль → approve → идемпотентный approve (1 активная
  подписка, не двоится) → reject отклонён на approved → reject + идемпотентный reject.
- Smoke: все модули импортируются, Dispatcher собирается на aiogram 3.21.

## Что НЕ входит (следующие этапы)
- Этап 2 — RBAC (user/moderator/admin/owner), защита Owner (код + триггер БД), полноценный аудит.
- Этап 3 — TelegramResolver, OSINT catalog-first.
- Этап 4 — рассылки, атомарные лимиты, бэкапы, PII, тесты + CI.
