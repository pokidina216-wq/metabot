#!/usr/bin/env bash
#
# Резервное копирование БД Vexis (PostgreSQL).
#
# Использование:
#   DATABASE_URL=postgres://user:pass@host:5432/db ./scripts/backup.sh [dest_dir]
#
# По умолчанию пишет gzip-дамп в ./backups. Для Railway удобно запускать как
# cron-job (railway run, либо отдельный scheduled service) и складывать в
# S3/совместимое хранилище. Дамп делается через pg_dump (формат custom -Fc
# или plain — здесь plain+gzip для простоты восстановления).
set -euo pipefail

DEST_DIR="${1:-./backups}"
mkdir -p "$DEST_DIR"

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "ERROR: DATABASE_URL не задан" >&2
  exit 1
fi

# Нормализуем DSN для pg_dump (убираем драйвер +asyncpg/+psycopg2, если есть).
DSN="${DATABASE_URL/+asyncpg/}"
DSN="${DSN/+psycopg2/}"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${DEST_DIR}/vexis_${TS}.sql.gz"

echo "→ Backup to ${OUT}"
pg_dump --no-owner --no-privileges "${DSN}" | gzip -9 > "${OUT}"
echo "✓ Done: $(du -h "${OUT}" | cut -f1)"

# Ротация: оставляем последние 14 дампов.
ls -1t "${DEST_DIR}"/vexis_*.sql.gz 2>/dev/null | tail -n +15 | xargs -r rm -f
echo "✓ Rotation kept latest 14 backups"
