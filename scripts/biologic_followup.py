"""Appointment and last J-code service context for biologic reminder events."""
from datetime import date, timedelta
import re
import tempfile
from pathlib import Path
import xml.etree.ElementTree as ET

PROCEDURES = {"21": "XOLAIR", "53": "FASENRA", "55": "TEZSPIRE"}
JCODES = {"J2357": "XOLAIR", "J0517": "FASENRA", "J2356": "TEZSPIRE"}
CLAIM_WINDOW_DAYS = 60
TITLE = "Biologic dispensed - no J-code claim after 60 days"
LEGACY_TITLE = "Biologic dispensed - no J-code claim after 30 days"
START = "\n[BIOLOGIC FOLLOW-UP]\n"
END = "\n[/BIOLOGIC FOLLOW-UP]"
FIELDS = ["missed_biologic_after_dispense", "missed_biologic_count",
          "missed_biologic_details", "matching_biologic_bookings_count",
          "canceled_biologic_details", "next_biologic_appointment",
          "next_any_appointment", "last_biologic_service_date",
          "last_biologic_service_office", "last_biologic_service_basis"]


def q(value):
    # dbisql normalizes literal CRLF in script text. Express CR explicitly so
    # native Windows staff notes retain their exact line endings and guards match.
    parts = str(value).split('\r')
    literals = ["'" + part.replace("\\", "\\\\").replace("'", "''") + "'" for part in parts]
    result = ' || CHAR(13) || '.join(literals)
    return '(' + result + ')' if len(parts) > 1 else result


def xml_select(select, sql):
    # dbisql console output truncates columns at 256 characters. XML OUTPUT
    # retrieves full values, including staff notes and trailing whitespace.
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as handle:
        path = Path(handle.name)
    path.unlink()
    try:
        select(sql.rstrip().rstrip(';') + f"; OUTPUT TO {q(path)} FORMAT XML;")
        path.chmod(0o600)
        # XML parsers normalize literal CR/CRLF. Character references preserve
        # the database value, unlike text-mode reads or direct ET.parse.
        raw = path.read_bytes().decode('utf-8').replace('\r', '&#13;')
        return [{c.attrib['name'].lower(): None if c.attrib.get('null') == 'true'
                 else (c.text or '') for c in row} for row in ET.fromstring(raw)]
    finally:
        path.unlink(missing_ok=True)


def query_followup(select, patient_ids):
    ids = sorted({int(p) for p in patient_ids})
    if not ids:
        return [], []
    id_sql = ",".join(map(str, ids))
    # Query every appointment for the scoped patients, including all procedure
    # slots. Future D rows exist in IMS: D alone is not proof of completion.
    sql = f"""
SELECT CAST('APPT|' || CAST(sd.forwhom_id AS VARCHAR(30)) || '|' ||
 CAST(sd.tran_id AS VARCHAR(30)) || '|' || CAST(sd.schedule_date AS VARCHAR(30)) || '|' ||
 COALESCE(CAST(sd.schedule_time AS VARCHAR(30)),'') || '|' || COALESCE(sd.status,'') || '|' ||
 COALESCE(CAST(sd.canceled_date AS VARCHAR(30)),'') || '|' || COALESCE(om.office_code,'UNKNOWN') || '|' ||
 COALESCE(sd.procedure_id,'') || '|' || COALESCE(sd.procedure_id_secondary,'') || '|' ||
 COALESCE(sd.procedure_id_tertiary,'') || '|' ||
 REPLACE(REPLACE(REPLACE(COALESCE(sp.description,'UNKNOWN'),'|',' '),CHAR(13),' '),CHAR(10),' ')
 AS VARCHAR(1000))
FROM schedule_detail sd LEFT JOIN office_master om ON om.srno=sd.office_id
LEFT JOIN schedule_procedure sp ON CAST(sp.procedure_id AS VARCHAR(30))=sd.procedure_id
WHERE sd.forwhom='P' AND sd.forwhom_id IN ({id_sql}) AND sd.schedule_date IS NOT NULL;
"""
    appointments = []
    for line in select(sql):
        if line.strip().startswith("APPT|"):
            parts = line.strip().split("|")
            if len(parts) != 12:
                raise RuntimeError("Invalid appointment context row")
            _, patient, tran, day, time, status, canceled, office, p1, p2, p3, descr = parts
            appointments.append({"patient_id": patient, "appointment_id": tran,
                "date": day, "time": time, "status": status, "canceled": canceled,
                "office": office, "procedures": [p1, p2, p3], "description": descr})
    services = []
    for line in select(f"""
SELECT 'SERVICE|' || CAST(bh.patient_id AS VARCHAR(30)) || '|' ||
 CAST(bd.service_date AS VARCHAR(30)) || '|' || bd.billing_id || '|' ||
 COALESCE(om.office_code,'UNKNOWN') || '|' || CAST(bd.tran_id AS VARCHAR(30)) || '|' ||
 CAST(bd.sr_id AS VARCHAR(30))
FROM billing_detail bd JOIN billing_header bh ON bh.tran_id=bd.tran_id
LEFT JOIN office_master om ON om.srno=bh.office_id
WHERE bh.patient_id IN ({id_sql}) AND bd.billing_id IN ('J2357','J0517','J2356')
 AND bd.service_date<=TODAY();
"""):
        if line.strip().startswith("SERVICE|"):
            parts = line.strip().split("|")
            if len(parts) != 7:
                raise RuntimeError("Invalid service context row")
            _, patient, day, jcode, office, tran, sr = parts
            services.append({"patient_id": patient, "date": day, "j_code": jcode,
                             "office": office, "tran_id": tran, "sr_id": sr})
    return appointments, services


def display(appt):
    return f"{appt['date']} {appt['time'][:5]} {appt['office']} {appt['description']}".strip()


def enrich(events, appointments, services, today):
    today_s = today.isoformat()
    for event in events:
        after = sorted((a for a in appointments if a["patient_id"] == event["patient_id"]
                        and a["date"] >= event["dispense_date"]),
                       key=lambda a: (a["date"], a["time"], int(a["appointment_id"])))
        matching = [a for a in after if any(PROCEDURES.get(p) == event["drug"]
                                            for p in a["procedures"])]
        missed = [a for a in matching if a["status"] == "M" and a["date"] <= today_s]
        canceled = [a for a in matching if a["canceled"] or a["status"] == "C"]
        active = [a for a in after if a["date"] >= today_s and not a["canceled"]
                  and a["status"] not in {"M", "C"}]
        next_bio = next((a for a in active if any(PROCEDURES.get(p) == event["drug"]
                                                for p in a["procedures"])), None)
        last = sorted((s for s in services if s["patient_id"] == event["patient_id"]
                       and s["j_code"] == event["j_code"] and s["date"] <= today_s),
                      key=lambda s: (s["date"], int(s["tran_id"]), int(s["sr_id"])))
        last_day = last[-1]["date"] if last else ""
        offices = sorted({s["office"] for s in last if s["date"] == last_day})
        event.update({
            "missed_biologic_after_dispense": "YES" if missed else "NO RECORDED MISSED APPOINTMENT",
            "missed_biologic_count": len(missed),
            "missed_biologic_details": "; ".join(display(a) for a in missed),
            "matching_biologic_bookings_count": len(matching),
            "canceled_biologic_details": "; ".join(display(a) for a in canceled),
            "next_biologic_appointment": display(next_bio) if next_bio else "NONE RECORDED",
            "next_any_appointment": display(active[0]) if active else "NONE RECORDED",
            "last_biologic_service_date": last_day,
            "last_biologic_service_office": "/".join(offices) if offices else "UNKNOWN",
            "last_biologic_service_basis": "Matching J-code service record; receipt not independently certified"
                if last else "No matching J-code service record found",
        })
        event["_appointments"] = matching
    return events


def context_note(event):
    from biologic_stock_returns import stock_note
    missed = event.get("missed_biologic_details", "") or "None recorded"
    # Complete appointment history stays in private CSV; keep native note <2000.
    if len(missed) > 400:
        missed = missed[:380].rsplit(";", 1)[0] + "; more in report"
    last = (f"{event.get('last_biologic_service_date') or 'Unknown date'} "
            f"{event.get('last_biologic_service_office', 'UNKNOWN')} "
            f"({event['j_code']} service record)")
    stock = stock_note(event) + "\n" if 'stock_return_status' in event else ''
    if event.get('monitoring_window_status') == 'OUTSIDE WINDOW':
        stock = (f"Outside rolling nine-month monitoring window (start {event['monitoring_start_date']}); "
                 "closed for queue scope, not evidence of a claim or stock return.\n" + stock)
    policy = ''
    if 'claim_window_status' in event:
        policy = (f"Claim window: {CLAIM_WINDOW_DAYS} days; deadline {event['deadline']}; "
                  f"first reminder date {event['first_reminder_date']}. "
                  f"{event['claim_window_status']}.\n")
    return (policy + stock + f"Missed {event['drug']} appointments after dispense: "
            f"{event.get('missed_biologic_count', 0)}. {missed}.\n"
            f"Biologic bookings after dispense: {event.get('matching_biologic_bookings_count', 0)}.\n"
            f"Next {event['drug']} appointment: {event.get('next_biologic_appointment', 'NONE RECORDED')}.\n"
            f"Next appointment of any type: {event.get('next_any_appointment', 'NONE RECORDED')}.\n"
            f"Last recorded {event['drug']} service: {last}. "
            "Scheduling alone does not establish drug receipt.")


def merge_note(old, context):
    if START in old or END in old:
        if old.count(START) != 1 or old.count(END) != 1 or old.index(END) < old.index(START):
            return None
        start, end = old.index(START), old.index(END) + len(END)
        result = old[:start] + START + context + END + old[end:]
    else:
        result = old + START + context + END
    return result if len(result) <= 2000 else None


def query_open_reminders(select, include_done=False):
    pending = """EXISTS(SELECT 1 FROM tobe_done_detail d WHERE d.todo_id=t.tran_id
 AND (d.task_status='P' OR d.task_status IS NULL OR d.task_status=''))"""
    scope = "t.status IN ('G','D')" if include_done else f"t.status='G' AND {pending}"
    sql = f"""
SELECT t.*,pm.patient_no AS context_patient_no,
 (SELECT MIN(d.show_date) FROM tobe_done_detail d WHERE d.todo_id=t.tran_id
  AND (d.task_status='P' OR d.task_status IS NULL OR d.task_status='')) AS context_show_min,
 (SELECT MAX(d.show_date) FROM tobe_done_detail d WHERE d.todo_id=t.tran_id
  AND (d.task_status='P' OR d.task_status IS NULL OR d.task_status='')) AS context_show_max,
 COALESCE(pm.lastname,'') || ', ' || COALESCE(pm.firstname,'') AS context_patient_name,
 CASE WHEN {pending} THEN 'OPEN' ELSE 'DONE' END AS context_task_state,
 (SELECT MAX(d.done_by) FROM tobe_done_detail d WHERE d.todo_id=t.tran_id
  AND d.task_status='D') AS context_done_by
FROM todo t JOIN patient_master pm ON pm.id=t.forwhom_id
WHERE t.source LIKE 'BIO_NO_CLAIM30:%' AND {scope};
"""
    reminders = []
    for row in xml_select(select, sql):
        tran, source, patient = row['tran_id'], row['source'], row['forwhom_id']
        old = row['note'] or ''
        match = re.fullmatch(r"BIO_NO_CLAIM30:(\d+):(J2357|J0517|J2356):(\d{8})", source)
        if not match or match[1] != patient:
            raise RuntimeError("Unexpected reminder source")
        day = date.fromisoformat(f"{match[3][:4]}-{match[3][4:6]}-{match[3][6:]}").isoformat()
        reminders.append({"todo_id": tran, "source": source, "patient_id": patient,
            "patient_no": row['context_patient_no'], "patient_name": row['context_patient_name'],
            "drug": JCODES[match[2]], "j_code": match[2], "dispense_date": day, "old_note": old,
            "old_title": row['todo'], "show_date_min": row['context_show_min'],
            "show_date_max": row['context_show_max'],
            "reminder_task_state": row['context_task_state'], "reminder_parent_status": row['status'],
            "reminder_completion_reason": row['context_done_by'] or ''})
    return reminders


def window_note(reminder):
    """Change only the recognizable generated legacy paragraph, retaining staff text."""
    old = reminder['old_note']
    day = date.fromisoformat(reminder['dispense_date'])
    pattern = (re.escape(f"{reminder['drug']} dispensed {day.isoformat()} (") +
               r"([A-Z0-9/]+)" + re.escape(f"). No subsequent {reminder['j_code']} claim "
               "is recorded after the full 30-day window. Review administration, "
               "billing/claim submission, supply source, or documented disposition. "
               f"Day-30 deadline: {(day + timedelta(days=30)).isoformat()}."))
    matches = list(re.finditer(pattern, old))
    if len(matches) != 1:
        return old
    match = matches[0]
    replacement = (f"{reminder['drug']} dispensed {day.isoformat()} ({match[1]}). "
                   f"Allow a full {CLAIM_WINDOW_DAYS}-day window for a subsequent "
                   f"{reminder['j_code']} claim. Review administration, billing/claim submission, "
                   "supply source, or documented disposition. "
                   f"Day-{CLAIM_WINDOW_DAYS} deadline: "
                   f"{(day + timedelta(days=CLAIM_WINDOW_DAYS)).isoformat()}.")
    return old[:match.start()] + replacement + old[match.end():]


def refresh_sql(reminder, new_note):
    # Exact note/title guards preserve concurrent staff edits. Policy migration
    # also updates the generated title and defers pending native dates atomically.
    # Completed detail history and staff-renamed titles are protected.
    policy_fields = ''
    title_guard = ''
    defer = reminder.get('defer_date')
    if 'old_title' in reminder:
        title = TITLE if reminder['old_title'] == LEGACY_TITLE else reminder['old_title']
        policy_fields = f",todo={q(title) if title is not None else 'NULL'}"
        title_guard = f" AND COALESCE(todo,'')={q(reminder['old_title'] or '')}"
    if defer:
        policy_fields += f",todo_date={q(defer)}"
    child_update = f"""
IF @@ROWCOUNT=1 THEN
 UPDATE tobe_done_detail SET show_date={q(defer)},todo_date={q(defer)},
 changed_date=CURRENT TIMESTAMP,changedby_id=-1
 WHERE todo_id={int(reminder['todo_id'])}
 AND (task_status='P' OR task_status IS NULL OR task_status='');
END IF;
""" if defer else ''
    return f"""
SET TEMPORARY OPTION auto_commit='Off';
SET TEMPORARY OPTION blocking_timeout='5000';
BEGIN
UPDATE todo SET note={q(new_note)},changed_date=CURRENT TIMESTAMP{policy_fields}
WHERE tran_id={int(reminder['todo_id'])} AND source={q(reminder['source'])}
 AND status='G' AND COALESCE(note,'')={q(reminder['old_note'])}{title_guard}
 AND EXISTS(SELECT 1 FROM tobe_done_detail d WHERE d.todo_id=todo.tran_id
 AND (d.task_status='P' OR d.task_status IS NULL OR d.task_status=''));
{child_update}
COMMIT;
END;
"""
