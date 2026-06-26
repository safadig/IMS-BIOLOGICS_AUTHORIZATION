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
SMS_ALERT_URL="${BIO_INS_CHANGE_SMS_ALERT_URL:-}"
if [[ -z "$SMS_ALERT_URL" && -n "${IMS_INSURANCE_AUDIT_ALERT_URL:-}" ]]; then
  SMS_ALERT_URL="${IMS_INSURANCE_AUDIT_ALERT_URL%/internal/*}/internal/queue-staff-notice"
fi
SMS_ALERT_TOKEN="${BIO_INS_CHANGE_SMS_ALERT_TOKEN:-${IMS_INSURANCE_AUDIT_ALERT_TOKEN:-}}"
SMS_ALERT_EMPLOYEES="${BIO_INS_CHANGE_SMS_ALERT_EMPLOYEES:-Johnson, Tara;Lisa;Cindy}"
SMS_ALERT_MESSAGE="${BIO_INS_CHANGE_SMS_ALERT_MESSAGE:-New biologic insurance/auth alert created in IMS. Check Biologics reminders now for PA/referral status before next buy-and-bill dose.}"

mkdir -p /opt/ims_router/logs /opt/ims_router/output

set -a
. /opt/ims_router/.env
set +a
. /opt/sqlanywhere17/bin64/sa_config.sh

conn="UID=${IMS_DB_USER};PWD=${IMS_DB_PASSWORD};ENG=${IMS_DB_ENGINE};DBN=${IMS_DB_NAME};LINKS=tcpip(host=${IMS_DB_HOST}:${IMS_DB_PORT});"

sql_file="$(mktemp /tmp/biologic_ins_change_reminders.XXXXXX.sql)"
output_file="$(mktemp /tmp/biologic_ins_change_reminders.XXXXXX.out)"
cleanup() {
  rm -f "$sql_file" "$output_file"
}
trap cleanup EXIT

sed \
  -e "s/__DISPENSE_LOOKBACK_DAYS__/${DISPENSE_LOOKBACK_DAYS}/g" \
  -e "s/__CHANGE_LOOKBACK_DAYS__/${CHANGE_LOOKBACK_DAYS}/g" \
  -e "s/__APPLY_EXTRA_WHERE__//g" \
  "$TEMPLATE" > "$sql_file"

"$DBISQL" -nogui -onerror exit -c "$conn" "$sql_file" | tee "$output_file"

inserted_count="$(awk '/^[0-9]+ row[(]s[)] inserted/ {print $1; exit}' "$output_file")"
if [[ "${inserted_count:-0}" =~ ^[0-9]+$ && "$inserted_count" -gt 0 ]]; then
  if [[ -z "$SMS_ALERT_URL" || -z "$SMS_ALERT_TOKEN" ]]; then
    echo "BIO_INS_CHANGE_SMS_ALERT skipped: alert URL/token not configured" >&2
    exit 0
  fi

  BIO_INS_CHANGE_INSERTED_COUNT="$inserted_count" \
  BIO_INS_CHANGE_SMS_ALERT_URL="$SMS_ALERT_URL" \
  BIO_INS_CHANGE_SMS_ALERT_TOKEN="$SMS_ALERT_TOKEN" \
  BIO_INS_CHANGE_SMS_ALERT_EMPLOYEES="$SMS_ALERT_EMPLOYEES" \
  BIO_INS_CHANGE_SMS_ALERT_MESSAGE="$SMS_ALERT_MESSAGE" \
  BIO_INS_CHANGE_SMS_ALERT_MARKER="bio_auth:$(date +%Y%m%d%H%M)" \
  python3 - <<'PY'
import json
import os
import sys
import urllib.error
import urllib.request

url = os.environ["BIO_INS_CHANGE_SMS_ALERT_URL"].strip()
token = os.environ["BIO_INS_CHANGE_SMS_ALERT_TOKEN"].strip()
employees = [
    item.strip()
    for item in os.environ.get("BIO_INS_CHANGE_SMS_ALERT_EMPLOYEES", "").replace("|", ";").split(";")
    if item.strip()
]
count = int(os.environ.get("BIO_INS_CHANGE_INSERTED_COUNT", "0") or "0")
message = os.environ["BIO_INS_CHANGE_SMS_ALERT_MESSAGE"].strip()
if count > 1:
    message = (
        f"{count} new biologic insurance/auth alerts created in IMS. "
        "Check Biologics reminders now for PA/referral status before next buy-and-bill dose."
    )
payload = {
    "employees": employees,
    "message": message,
    "marker_prefix": os.environ["BIO_INS_CHANGE_SMS_ALERT_MARKER"],
    "source": "BIO_INS_CHANGE_PA",
}
req = urllib.request.Request(
    url,
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "X-Alert-Token": token,
    },
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=20) as response:
        body = response.read().decode("utf-8", errors="replace")
except urllib.error.HTTPError as exc:
    body = exc.read().decode("utf-8", errors="replace")
    print(f"BIO_INS_CHANGE_SMS_ALERT failed HTTP {exc.code}: {body}", file=sys.stderr)
    sys.exit(1)
except Exception as exc:
    print(f"BIO_INS_CHANGE_SMS_ALERT failed: {exc}", file=sys.stderr)
    sys.exit(1)
print(f"BIO_INS_CHANGE_SMS_ALERT queued: {body}")
PY
fi
