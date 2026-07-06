#!/usr/bin/env bash
set -euo pipefail

LOCK_FILE="${BIOLOGIC_INS_CHANGE_REMINDER_LOCK_FILE:-/opt/ims_router/biologic_ins_change_reminders.lock}"
if [[ "${BIOLOGIC_INS_CHANGE_REMINDER_LOCK_HELD:-0}" != "1" ]]; then
  exec /usr/bin/flock -n "$LOCK_FILE" env BIOLOGIC_INS_CHANGE_REMINDER_LOCK_HELD=1 "$0" "$@"
fi

set -a
. /opt/ims_router/.env
set +a
. /opt/sqlanywhere17/bin64/sa_config.sh

DISPENSE_LOOKBACK_DAYS="${BIOLOGIC_INS_CHANGE_DISPENSE_LOOKBACK_DAYS:-45}"
CHANGE_LOOKBACK_DAYS="${BIOLOGIC_INS_CHANGE_LOOKBACK_DAYS:-3}"
RUN_STARTED_TS="$(date '+%Y-%m-%d %H:%M:%S')"
TEMPLATE="${BIOLOGIC_INS_CHANGE_TEMPLATE:-/opt/ims_router/sql/create_biologic_insurance_change_reminders.sql}"
DBISQL="${IMS_DBISQL:-/opt/sqlanywhere17/bin64/dbisql}"
SMS_ALERT_URL="${BIO_INS_CHANGE_SMS_ALERT_URL:-}"
if [[ -z "$SMS_ALERT_URL" && -n "${IMS_INSURANCE_AUDIT_ALERT_URL:-}" ]]; then
  SMS_ALERT_URL="${IMS_INSURANCE_AUDIT_ALERT_URL%/internal/*}/internal/queue-staff-notice"
fi
SMS_ALERT_TOKEN="${BIO_INS_CHANGE_SMS_ALERT_TOKEN:-${IMS_INSURANCE_AUDIT_ALERT_TOKEN:-}}"
SMS_ALERT_EMPLOYEES="${BIO_INS_CHANGE_SMS_ALERT_EMPLOYEES:-Johnson, Tara;Lisa;Cindy}"
SMS_ALERT_MESSAGE="${BIO_INS_CHANGE_SMS_ALERT_MESSAGE:-New biologic insurance/auth alert created in IMS. Check Biologics reminders now for PA/referral status before next buy-and-bill dose.}"
SMS_USER_ALERT_MESSAGE="${BIO_INS_CHANGE_SMS_USER_ALERT_MESSAGE:-You changed insurance for a patient receiving a buy-and-bill biologic. It is essential that you notify Tara that the change occurred so new prior authorization can be obtained. Check IMS Biologics reminders now.}"

mkdir -p /opt/ims_router/logs /opt/ims_router/output

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
  -e "s/__RUN_STARTED_TS__/${RUN_STARTED_TS}/g" \
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
  BIO_INS_CHANGE_SMS_USER_ALERT_MESSAGE="$SMS_USER_ALERT_MESSAGE" \
  BIO_INS_CHANGE_SMS_ALERT_MARKER="bio_auth:$(date +%Y%m%d%H%M)" \
  BIO_INS_CHANGE_OUTPUT_FILE="$output_file" \
  python3 - <<'PY'
import json
import os
import sys
import urllib.error
import urllib.request

url = os.environ["BIO_INS_CHANGE_SMS_ALERT_URL"].strip()
token = os.environ["BIO_INS_CHANGE_SMS_ALERT_TOKEN"].strip()
marker_base = os.environ["BIO_INS_CHANGE_SMS_ALERT_MARKER"]


def split_employees(value):
    return [item.strip() for item in value.replace("|", ";").split(";") if item.strip()]


def append_unique(values, value):
    clean = " ".join((value or "").strip().split())
    if clean and clean.casefold() not in {item.casefold() for item in values}:
        values.append(clean)


def prefixed_parts(text, prefix):
    if prefix not in text:
        return None
    return text[text.index(prefix) :].split("|")


def patient_no_text(patient_numbers):
    if not patient_numbers:
        return ""
    shown = patient_numbers[:8]
    text = ", ".join(shown)
    remaining = len(patient_numbers) - len(shown)
    if remaining > 0:
        text = f"{text} +{remaining} more"
    return text


def post_notice(employees, message, marker_prefix, label):
    if not employees:
        print(f"{label} skipped: no employees")
        return
    payload = {
        "employees": employees,
        "message": message,
        "marker_prefix": marker_prefix,
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
        print(f"{label} failed HTTP {exc.code}: {body}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"{label} failed: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = {}
    if payload.get("ok") is False:
        print(f"{label} failed gateway response: {body}", file=sys.stderr)
        sys.exit(1)
    print(f"{label} queued: {body}")


count = int(os.environ.get("BIO_INS_CHANGE_INSERTED_COUNT", "0") or "0")
user_alerts = {}
patient_numbers = []
output_path = os.environ.get("BIO_INS_CHANGE_OUTPUT_FILE", "")
if output_path and os.path.exists(output_path):
    with open(output_path, "r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            text = line.strip()
            patient_parts = prefixed_parts(text, "BIO_INS_CHANGE_PATIENT_ALERT|")
            if patient_parts and len(patient_parts) >= 2:
                append_unique(patient_numbers, patient_parts[1])
                continue

            user_parts = prefixed_parts(text, "BIO_INS_CHANGE_USER_ALERT|")
            if user_parts and len(user_parts) >= 2:
                employee = user_parts[1].strip()
                patient_no = user_parts[3].strip() if len(user_parts) >= 4 else ""
                if employee:
                    user_alerts.setdefault(employee, [])
                    append_unique(user_alerts[employee], patient_no)

patient_text = patient_no_text(patient_numbers)
if patient_text:
    if count > 1:
        ops_message = (
            f"{count} new biologic insurance/auth alerts created in IMS for patient_no {patient_text}. "
            "Check Biologics reminders now for PA/referral status before next buy-and-bill dose."
        )
    else:
        ops_message = (
            f"New biologic insurance/auth alert created in IMS for patient_no {patient_text}. "
            "Check Biologics reminders now for PA/referral status before next buy-and-bill dose."
        )
elif count > 1:
    ops_message = (
        f"{count} new biologic insurance/auth alerts created in IMS. "
        "Check Biologics reminders now for PA/referral status before next buy-and-bill dose."
    )
else:
    ops_message = os.environ["BIO_INS_CHANGE_SMS_ALERT_MESSAGE"].strip()
post_notice(
    split_employees(os.environ.get("BIO_INS_CHANGE_SMS_ALERT_EMPLOYEES", "")),
    ops_message,
    marker_base,
    "BIO_INS_CHANGE_SMS_ALERT",
)

if user_alerts:
    for employee, employee_patient_numbers in sorted(user_alerts.items()):
        employee_patient_text = patient_no_text(employee_patient_numbers)
        if employee_patient_text:
            user_message = (
                f"You changed insurance for patient_no {employee_patient_text}, who receives a buy-and-bill biologic. "
                "It is essential that you notify Tara that the change occurred so new prior authorization can be obtained. "
                "Check IMS Biologics reminders now."
            )
        else:
            user_message = os.environ["BIO_INS_CHANGE_SMS_USER_ALERT_MESSAGE"].strip()
        post_notice(
            [employee],
            user_message,
            f"{marker_base}:user",
            "BIO_INS_CHANGE_USER_SMS_ALERT",
        )
else:
    print("BIO_INS_CHANGE_USER_SMS_ALERT skipped: IMS audit user unavailable for new reminder(s)")
PY
fi
