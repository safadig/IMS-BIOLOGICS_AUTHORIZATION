#!/usr/bin/env bash
set -euo pipefail

LOCK_FILE="${BIOLOGIC_INS_CHANGE_REMINDER_LOCK_FILE:-/opt/ims_router/biologic_ins_change_reminders.lock}"
if [[ "${BIOLOGIC_INS_CHANGE_REMINDER_LOCK_HELD:-0}" != "1" ]]; then
  exec /usr/bin/flock -n "$LOCK_FILE" env BIOLOGIC_INS_CHANGE_REMINDER_LOCK_HELD=1 "$0" "$@"
fi

DISPENSE_LOOKBACK_DAYS="${BIOLOGIC_INS_CHANGE_DISPENSE_LOOKBACK_DAYS:-45}"
CHANGE_LOOKBACK_DAYS="${BIOLOGIC_INS_CHANGE_LOOKBACK_DAYS:-3}"
TEMPLATE="${BIOLOGIC_INS_CHANGE_TEMPLATE:-/opt/ims_router/sql/create_biologic_insurance_change_reminders.sql}"
DBISQL="${IMS_DBISQL:-/opt/sqlanywhere17/bin64/dbisql}"

mkdir -p /opt/ims_router/logs /opt/ims_router/output

set -a
. /opt/ims_router/.env
set +a
. /opt/sqlanywhere17/bin64/sa_config.sh

conn="UID=${IMS_DB_USER};PWD=${IMS_DB_PASSWORD};ENG=${IMS_DB_ENGINE};DBN=${IMS_DB_NAME};LINKS=tcpip(host=${IMS_DB_HOST}:${IMS_DB_PORT});"

sql_file="$(mktemp /tmp/biologic_ins_change_reminders.XXXXXX.sql)"
cleanup() {
  rm -f "$sql_file"
}
trap cleanup EXIT

sed \
  -e "s/__DISPENSE_LOOKBACK_DAYS__/${DISPENSE_LOOKBACK_DAYS}/g" \
  -e "s/__CHANGE_LOOKBACK_DAYS__/${CHANGE_LOOKBACK_DAYS}/g" \
  -e "s/__APPLY_EXTRA_WHERE__//g" \
  "$TEMPLATE" > "$sql_file"

"$DBISQL" -nogui -onerror exit -c "$conn" "$sql_file"

