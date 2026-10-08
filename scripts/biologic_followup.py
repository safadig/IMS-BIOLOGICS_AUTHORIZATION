"""Appointment and last J-code service context for biologic reminder events."""
from datetime import date
import re
import tempfile
from pathlib import Path
import xml.etree.ElementTree as ET

PROCEDURES = {"21": "XOLAIR", "53": "FASENRA", "55": "TEZSPIRE"}
JCODES = {"J2357": "XOLAIR", "J0517": "FASENRA", "J2356": "TEZSPIRE"}
START = "\n[BIOLOGIC FOLLOW-UP]\n"
END = "\n[/BIOLOGIC FOLLOW-UP]"
FIELDS = ["missed_biologic_after_dispense", "missed_biologic_count",
          "missed_biologic_details", "matching_biologic_bookings_count",
          "canceled_biologic_details", "next_biologic_appointment",
          "next_any_appointment", "last_biologic_service_date",
          "last_biologic_service_office", "last_biologic_service_basis"]


def q(value):
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


def xml_select(select, sql):
    # dbisql console output truncates columns at 256 characters. XML OUTPUT
    # retrieves full values, including staff notes and trailing whitespace.
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as handle:
        path = Path(handle.name)
    path.unlink()
    try:
        select(sql.rstrip().rstrip(';') + f"; OUTPUT TO {q(path)} FORMAT XML;")
        path.chmod(0o600)
        return [{c.attrib['name'].lower(): None if c.attrib.get('null') == 'true'
                 else (c.text or '') for c in row} for row in ET.parse(path).getroot()]
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
    missed = event.get("missed_biologic_details", "") or "None recorded"
    # Complete appointment history stays in private CSV; keep native note <2000.
    if len(missed) > 400:
        missed = missed[:380].rsplit(";", 1)[0] + "; more in report"
    last = (f"{event.get('last_biologic_service_date') or 'Unknown date'} "
            f"{event.get('last_biologic_service_office', 'UNKNOWN')} "
            f"({event['j_code']} service record)")
    return (f"Missed {event['drug']} appointments after dispense: "
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


def query_open_reminders(select):
    sql = """
SELECT t.*,pm.patient_no AS context_patient_no,
 COALESCE(pm.lastname,'') || ', ' || COALESCE(pm.firstname,'') AS context_patient_name
FROM todo t JOIN patient_master pm ON pm.id=t.forwhom_id
WHERE t.source LIKE 'BIO_NO_CLAIM30:%' AND t.status='G'
 AND EXISTS(SELECT 1 FROM tobe_done_detail d WHERE d.todo_id=t.tran_id
 AND (d.task_status='P' OR d.task_status IS NULL OR d.task_status=''));
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
            "drug": JCODES[match[2]], "j_code": match[2], "dispense_date": day, "old_note": old})
    return reminders


def refresh_sql(reminder, new_note):
    # Optimistic exact-note check preserves concurrent staff edits. Only note and
    # audit timestamp change; all-done detail history is protected in the write.
    return f"""
SET TEMPORARY OPTION blocking_timeout='5000';
UPDATE todo SET note={q(new_note)},changed_date=CURRENT TIMESTAMP
WHERE tran_id={int(reminder['todo_id'])} AND source={q(reminder['source'])}
 AND status='G' AND COALESCE(note,'')={q(reminder['old_note'])}
 AND EXISTS(SELECT 1 FROM tobe_done_detail d WHERE d.todo_id=todo.tran_id
 AND (d.task_status='P' OR d.task_status IS NULL OR d.task_status=''));
COMMIT;
"""
