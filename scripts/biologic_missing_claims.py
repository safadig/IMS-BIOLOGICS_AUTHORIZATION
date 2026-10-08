#!/usr/bin/env python3
"""Nightly IMS dispense-to-claim check; reports are private, logs are aggregate."""
from __future__ import annotations

import argparse
import calendar
import csv
import json
import os
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from biologic_followup import CLAIM_WINDOW_DAYS, TITLE

DRUGS = {
    "XOL75": ("XOLAIR", "J2357", 14),
    "XO150": ("XOLAIR", "J2357", 14),
    "XO300": ("XOLAIR", "J2357", 14),
    "FAS30": ("FASENRA", "J0517", 81),
    "FASDI": ("FASENRA", "J0517", 81),
    "TEZ": ("TEZSPIRE", "J2356", 123),
}
# Stable identity retained across the 30-to-60-day policy change for deduplication.
SOURCE = "BIO_NO_CLAIM30"


def monitoring_cutoff(today, months=9):
    year, month0 = divmod(today.year * 12 + today.month - 1 - months, 12)
    month = month0 + 1
    return date(year, month, min(today.day, calendar.monthrange(year, month)[1]))


def sql_string(value):
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


def db_helpers():
    sys.path.insert(0, "/opt/ims_router")
    from ims_workload_monitor import run_select_lines, run_dbisql_file
    return run_select_lines, run_dbisql_file


def query_rows():
    select, _ = db_helpers()
    codes = ",".join(sql_string(c) for c in list(DRUGS) + ["J2357", "J0517", "J2356"])
    # claim_tracker.billing_id/service_from can be NULL even on paid claims.
    # Link the live J-code billing line using tran_id/sr_id, then claim_detail.
    sql = f"""
SELECT 'DATA|' || CAST(bh.patient_id AS VARCHAR(30)) || '|' ||
 CAST(pm.patient_no AS VARCHAR(30)) || '|' ||
 REPLACE(REPLACE(REPLACE(COALESCE(pm.lastname,'') || ', ' ||
 COALESCE(pm.firstname,''),'|',' '),CHAR(13),' '),CHAR(10),' ') || '|' ||
 CAST(bd.service_date AS VARCHAR(30)) || '|' || bd.billing_id || '|' ||
 CAST(bd.tran_id AS VARCHAR(30)) || '|' || CAST(bd.sr_id AS VARCHAR(30)) || '|' ||
 CASE WHEN EXISTS(SELECT 1 FROM claim_tracker ct
 JOIN claim_detail cd ON cd.claim_id=ct.claim_id
 WHERE ct.tran_id=bd.tran_id AND ct.sr_id=bd.sr_id) THEN 'Y' ELSE 'N' END
FROM billing_detail bd
JOIN billing_header bh ON bh.tran_id=bd.tran_id
JOIN patient_master pm ON pm.id=bh.patient_id
WHERE bd.billing_id IN ({codes}) AND bd.service_date<=TODAY();
"""
    rows = []
    for line in select(sql):
        if line.strip().startswith("DATA|"):
            row = line.strip().split("|")
            if len(row) != 9:
                raise RuntimeError("Invalid source row; no reminders written")
            rows.append(row)
    return rows


def evaluate(rows, today, start_date):
    events, claims, posted = {}, defaultdict(list), defaultdict(list)
    for r in rows:
        _, patient, account, name, dos, code, tran, sr, has_claim = r
        day = date.fromisoformat(dos)
        if code in DRUGS:
            if day < start_date:
                continue
            drug, jcode, category = DRUGS[code]
            key = (patient, drug, dos)
            event = events.setdefault(key, {
                "patient_id": patient, "patient_no": account, "patient_name": name,
                "drug": drug, "j_code": jcode, "category_id": category,
                "dispense_date": dos, "codes": [], "tran_id": tran, "sr_id": sr,
                "source": f"{SOURCE}:{patient}:{jcode}:{dos.replace('-', '')}",
            })
            if code not in event["codes"]:
                event["codes"].append(code)
        else:
            posted[(patient, code)].append(day)
            if has_claim == "Y":
                claims[(patient, code)].append(day)
    missing, late = [], []
    for event in events.values():
        day = date.fromisoformat(event["dispense_date"])
        deadline = day + timedelta(days=CLAIM_WINDOW_DAYS)
        if today <= deadline:  # Give the complete day-60 service window.
            continue
        following = sorted(d for d in claims[(event["patient_id"], event["j_code"])]
                           if day <= d <= today)
        if following and following[0] <= deadline:
            continue
        event["deadline"] = deadline.isoformat()
        event["days_since_dispense"] = (today - day).days
        event["later_claim_date"] = following[0].isoformat() if following else ""
        unclaimed = sorted(d for d in posted[(event["patient_id"], event["j_code"])]
                           if day <= d <= today)
        event["later_posted_j_date"] = unclaimed[0].isoformat() if unclaimed else ""
        (late if following else missing).append(event)
    return sorted(missing, key=lambda e: (e["patient_no"], e["dispense_date"])), late


def enrich_window(events, rows, today):
    claims = defaultdict(list)
    for r in rows:
        if r[5] in {'J2357', 'J0517', 'J2356'} and r[8] == 'Y':
            claims[(r[1], r[5])].append(date.fromisoformat(r[4]))
    for event in events:
        day = date.fromisoformat(event['dispense_date'])
        deadline = day + timedelta(days=CLAIM_WINDOW_DAYS)
        following = sorted(d for d in claims[(event['patient_id'], event['j_code'])]
                           if day <= d <= today)
        event['deadline'] = deadline.isoformat()
        event['first_reminder_date'] = (deadline + timedelta(days=1)).isoformat()
        event['days_since_dispense'] = (today - day).days
        event['claim_window_status'] = ('MATCHING CLAIM RECORDED' if following else
            'WAITING FOR FULL WINDOW' if today <= deadline else 'WINDOW ELAPSED; NO MATCHING CLAIM')
        event['qualifying_claim_date'] = following[0].isoformat() if following else ''
        # Defer existing pending reminders rather than completing them and losing future alerts.
        if (event.get('reminder_task_state') == 'OPEN' and today <= deadline
                and not following and (event.get('show_date_min') != event['first_reminder_date']
                                       or event.get('show_date_max') != event['first_reminder_date'])):
            event['defer_date'] = event['first_reminder_date']


def complete_claim_sql(reminder, expected_note):
    from biologic_stock_returns import completion_sql
    if not reminder.get('qualifying_claim_date'):
        raise ValueError('A linked matching claim is required')
    predicate = f"""EXISTS(SELECT 1 FROM billing_detail bd
 JOIN billing_header bh ON bh.tran_id=bd.tran_id
 JOIN claim_tracker ct ON ct.tran_id=bd.tran_id AND ct.sr_id=bd.sr_id
 JOIN claim_detail cd ON cd.claim_id=ct.claim_id
 WHERE bh.patient_id={int(reminder['patient_id'])} AND bd.billing_id={sql_string(reminder['j_code'])}
 AND bd.service_date BETWEEN {sql_string(reminder['dispense_date'])} AND TODAY())"""
    return completion_sql(reminder, expected_note, predicate, 'Biologic matching claim monitor')


def write_report(path, events):
    from biologic_followup import FIELDS
    from biologic_stock_returns import FIELDS as STOCK_FIELDS
    fields = ["patient_no", "patient_name", "drug", "codes", "dispense_date",
              "deadline", "days_since_dispense", "j_code", "later_claim_date",
              "later_posted_j_date", "source", "todo_id", "reminder_task_state",
              "reminder_completion_reason", "monitoring_start_date", "monitoring_window_status",
              "first_reminder_date", "claim_window_status", "qualifying_claim_date",
              "show_date_min", "show_date_max"] + FIELDS + STOCK_FIELDS
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for event in events:
            writer.writerow({**event, "codes": "/".join(event.get("codes", []))})
    path.chmod(0o600)


def insert_sql(event):
    from biologic_followup import START, END, context_note
    from biologic_stock_returns import fully_returned_sql
    q = sql_string
    note = (f"{event['drug']} dispensed {event['dispense_date']} "
            f"({'/'.join(event['codes'])}). No subsequent {event['j_code']} claim "
            f"is recorded after the full {CLAIM_WINDOW_DAYS}-day window. Review administration, "
            "billing/claim submission, supply source, or documented disposition. "
            f"Day-{CLAIM_WINDOW_DAYS} deadline: {event['deadline']}. "
            "A billing line without a linked claim does not satisfy this check.")
    if event["later_posted_j_date"]:
        note += f" A J-code charge exists dated {event['later_posted_j_date']}; review claim linkage."
    if "missed_biologic_count" in event:
        note += START + context_note(event) + END
    patient_display = f"{event['patient_name']}  ({event['patient_no']})"
    dispense_codes = ",".join(q(code) for code, values in DRUGS.items()
                              if values[0] == event["drug"])
    # Both inserts commit together. Use the native detail autoincrement default
    # instead of MAX+1 or a table lock; dedupe includes staff-completed rows.
    return f"""
SET TEMPORARY OPTION auto_commit='Off';
SET TEMPORARY OPTION blocking_timeout='5000';
INSERT INTO todo (
 tran_id,tran_date,status,forwhom,forwhom_name,category_id,todo,todo_at,
 todo_date,todo_time,tobe_doneby,generated_by,priority,todo_by,todo_by_id,
 todo_by_multi_id,todo_by_multi_group,assignto_flag,forwhom_id,ref_tran_id,
 ref_sr_id,note,source,is_auto,reference_flag,created_date,changed_date,createdby_id,changedby_id)
SELECT seq_todo_id.NEXTVAL,TODAY(),'G','P',{q(patient_display)},
 {event['category_id']},{q(TITLE)},'T',
 TODAY(),CURRENT TIME,'Safadi, Ghassan * Clark, Rachel','U','H','U',NULL,
 ',1,24,',NULL,'N',{int(event['patient_id'])},{int(event['tran_id'])},
 {int(event['sr_id'])},{q(note)},{q(event['source'])},'N','1',
 CURRENT TIMESTAMP,CURRENT TIMESTAMP,-1,-1
WHERE NOT EXISTS(SELECT 1 FROM todo WHERE source={q(event['source'])})
 AND TODAY()>DATEADD(day,{CLAIM_WINDOW_DAYS},{q(event['dispense_date'])})
 AND EXISTS(SELECT 1 FROM billing_detail bd
 JOIN billing_header bh ON bh.tran_id=bd.tran_id
    WHERE bh.patient_id={int(event['patient_id'])}
 AND bd.service_date={q(event['dispense_date'])}
 AND bd.billing_id IN ({dispense_codes}))
 AND NOT {fully_returned_sql(event)}
 AND NOT EXISTS(SELECT 1 FROM billing_detail bd
 JOIN billing_header bh ON bh.tran_id=bd.tran_id
 JOIN claim_tracker ct ON ct.tran_id=bd.tran_id AND ct.sr_id=bd.sr_id
 JOIN claim_detail cd ON cd.claim_id=ct.claim_id
 WHERE bh.patient_id={int(event['patient_id'])} AND bd.billing_id={q(event['j_code'])}
 AND bd.service_date BETWEEN {q(event['dispense_date'])} AND TODAY());
INSERT INTO tobe_done_detail (
 todo_id,todo_date,todo_at,iteration,task_status,show_date,
 created_date,changed_date,createdby_id,changedby_id)
SELECT t.tran_id,t.todo_date,t.todo_at,t.iteration,'P',TODAY(),
 CURRENT TIMESTAMP,CURRENT TIMESTAMP,-1,-1
FROM todo t WHERE t.source={q(event['source'])}
 AND t.status='G' AND NOT EXISTS(SELECT 1 FROM tobe_done_detail d WHERE d.todo_id=t.tran_id);
COMMIT;
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--start-date", default=None,
                        help="Rolling nine calendar months by default; earlier override is dry-run only")
    parser.add_argument("--output-dir", default="/opt/ims_router/output/biologic_missing_claims")
    args = parser.parse_args()
    os.umask(0o077)
    today = date.today()
    cutoff = monitoring_cutoff(today)
    start_date = date.fromisoformat(args.start_date) if args.start_date else cutoff
    if args.apply:
        start_date = max(start_date, cutoff)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    select, execute = db_helpers()
    from biologic_return_visit_cleanup import cleanup_return_visits
    cleanup_summary = cleanup_return_visits(select, execute, out, args.apply)
    rows = query_rows()
    missing, late = evaluate(rows, today, start_date)
    from biologic_followup import (query_open_reminders, query_followup, enrich,
                                   context_note, merge_note, refresh_sql, window_note, LEGACY_TITLE)
    all_reminders = query_open_reminders(select, include_done=True)
    reminders = [r for r in all_reminders if r['reminder_task_state'] == 'OPEN'
                 and r['reminder_parent_status'] == 'G']
    enrich_window(missing + late + all_reminders, rows, today)
    appointments, services = query_followup(select, [e['patient_id'] for e in missing + late + all_reminders])
    enrich(missing + late + all_reminders, appointments, services, today)
    from biologic_stock_returns import query_stock, enrich_stock, complete_return_sql, complete_window_sql
    dispenses, returns = query_stock(select, [e['patient_id'] for e in missing + late + all_reminders])
    enrich_stock(missing + late + all_reminders, dispenses, returns, today)
    for event in missing + late + all_reminders:
        event['monitoring_start_date'] = cutoff.isoformat()
        event['monitoring_window_status'] = ('OUTSIDE WINDOW' if event['dispense_date'] < cutoff.isoformat()
                                             else 'INSIDE WINDOW')
    outside = [r for r in reminders if r['monitoring_window_status'] == 'OUTSIDE WINDOW']
    outside_report = {r['source']: r for r in all_reminders if
                      r['reminder_completion_reason'] == 'Biologic monitoring window'}
    outside_report.update({r['source']: r for r in outside})
    returned_events = [e for e in missing if e['stock_return_status'] == 'FULLY RETURNED TO STOCK']
    missing = [e for e in missing if e['stock_return_status'] != 'FULLY RETURNED TO STOCK']
    # Preserve already-created reminders and add verified return disposition to their note.
    returned_report = {e['source']: e for e in returned_events}
    returned_report.update({e['source']: e for e in all_reminders
                            if e['stock_return_status'] == 'FULLY RETURNED TO STOCK'})
    write_report(out / "missing-current.csv", missing)
    write_report(out / "late-claims.csv", late)
    write_report(out / "open-reminder-context.csv", reminders)
    write_report(out / "returned-to-stock.csv", list(returned_report.values()))
    write_report(out / 'outside-window.csv', list(outside_report.values()))
    history = out / "appointment-history.csv"
    with history.open("w", newline="", encoding="utf-8-sig") as handle:
        fields = ["patient_no", "source", "dispense_date", "drug", "appointment_id",
                  "date", "time", "office", "status", "canceled", "description"]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        history_events = {e['source']: e for e in missing + late + returned_events + all_reminders}
        for event in history_events.values():
            for appt in event['_appointments']:
                writer.writerow({**event, **appt})
    history.chmod(0o600)
    # Verify the exact expected recipient identities every run; fail closed.
    identities = [line.strip() for line in select("""
SELECT 'EMP|' || CAST(empid AS VARCHAR(20)) || '|' || COALESCE(firstname,'') ||
 '|' || COALESCE(lastname,'') FROM emp_master WHERE empid IN (1,24);
""") if line.strip().startswith("EMP|")]
    if set(identities) != {"EMP|1|Ghassan|Safadi", "EMP|24|Rachel|Clark"}:
        raise RuntimeError("Recipient identity changed; no reminders written")
    existing = set()
    for line in select(f"SELECT 'KEY|' || source FROM todo WHERE source LIKE '{SOURCE}:%';"):
        if line.strip().startswith("KEY|"):
            existing.add(line.strip()[4:])
    new = [e for e in missing if e["source"] not in existing]
    updated = 0
    skipped = 0
    completed = 0
    completed_outside = 0
    completed_claim = 0
    deferred = 0
    if args.apply:
        for event in new:
            with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as handle:
                handle.write(insert_sql(event))
                sql_path = handle.name
            try:
                result = execute(sql_path)
                if result.returncode:
                    raise RuntimeError("IMS reminder transaction failed; inspect private evidence")
            finally:
                Path(sql_path).unlink(missing_ok=True)
        expected = {e["source"] for e in new}
        verified = set()
        for line in select(f"""
SELECT 'OK|' || t.source FROM todo t JOIN tobe_done_detail d ON d.todo_id=t.tran_id
WHERE t.source LIKE '{SOURCE}:%' AND t.todo_by_multi_id=',1,24,'
 AND t.status='G' AND d.task_status='P' AND d.show_date<=TODAY();
"""):
            if line.strip().startswith("OK|"):
                verified.add(line.strip()[3:])
        if not expected <= verified:
            raise RuntimeError("Reminder readback failed")
        planned_updates = []
        for reminder in reminders:
            note = merge_note(window_note(reminder), context_note(reminder))
            if note is None:
                skipped += 1
                continue
            if (note == reminder['old_note'] and reminder['old_title'] != LEGACY_TITLE
                    and not reminder.get('defer_date')):
                continue
            planned_updates.append((reminder, note))
        if planned_updates:
            with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as handle:
                for reminder, note in planned_updates:
                    handle.write(refresh_sql(reminder, note))
                sql_path = handle.name
            try:
                result = execute(sql_path)
                if result.returncode:
                    raise RuntimeError("IMS context refresh failed")
            finally:
                Path(sql_path).unlink(missing_ok=True)
            current = {r['todo_id']: r for r in query_open_reminders(select)}
            for reminder, note in planned_updates:
                actual = current.get(reminder['todo_id'], {})
                expected_title = TITLE if reminder['old_title'] == LEGACY_TITLE else reminder['old_title']
                if (actual.get('old_note') != note or actual.get('old_title') != expected_title
                        or (reminder.get('defer_date') and
                            (actual.get('show_date_min') != reminder['defer_date'] or
                             actual.get('show_date_max') != reminder['defer_date']))):
                    raise RuntimeError("Reminder context changed concurrently; inspect before retry")
                reminder['show_date_min'] = actual['show_date_min']
                reminder['show_date_max'] = actual['show_date_max']
                deferred += int(bool(reminder.get('defer_date')))
                updated += 1
        # Gus authorized completing these monitor reminders when fully returned.
        # The live predicate and exact-note guard protect concurrent staff changes.
        for reminder in reminders:
            is_return = reminder['stock_return_status'] == 'FULLY RETURNED TO STOCK'
            is_outside = reminder['monitoring_window_status'] == 'OUTSIDE WINDOW'
            is_claim = bool(reminder.get('qualifying_claim_date'))
            if not is_return and not is_outside and not is_claim:
                continue
            expected_note = merge_note(window_note(reminder), context_note(reminder))
            if expected_note is None:
                continue
            with tempfile.NamedTemporaryFile('w', suffix='.sql', delete=False) as handle:
                handle.write(complete_return_sql(reminder, expected_note) if is_return
                             else complete_window_sql(reminder, expected_note, cutoff) if is_outside
                             else complete_claim_sql(reminder, expected_note))
                sql_path = handle.name
            try:
                result = execute(sql_path)
                if result.returncode:
                    raise RuntimeError('Reminder disposition completion failed')
            finally:
                Path(sql_path).unlink(missing_ok=True)
            verified_done = [s.strip() for s in select(f"""
SELECT 'DONE|' || CAST(t.tran_id AS VARCHAR(30)) FROM todo t
WHERE t.tran_id={int(reminder['todo_id'])} AND t.source={sql_string(reminder['source'])}
 AND NOT EXISTS(SELECT 1 FROM tobe_done_detail d WHERE d.todo_id=t.tran_id
 AND (d.task_status='P' OR d.task_status IS NULL OR d.task_status=''))
 AND EXISTS(SELECT 1 FROM tobe_done_detail d WHERE d.todo_id=t.tran_id
 AND d.task_status='D' AND d.done_by={sql_string('Biologic stock return monitor' if is_return
                                              else 'Biologic monitoring window' if is_outside
                                              else 'Biologic matching claim monitor')});
""") if s.strip().startswith('DONE|')]
            if verified_done != [f"DONE|{reminder['todo_id']}"]:
                raise RuntimeError('Reminder completion readback failed')
            reminder['reminder_task_state'] = 'DONE'
            reminder['reminder_completion_reason'] = ('Biologic stock return monitor' if is_return
                                                      else 'Biologic monitoring window' if is_outside
                                                      else 'Biologic matching claim monitor')
            completed += int(is_return)
            completed_outside += int(not is_return and is_outside)
            completed_claim += int(not is_return and not is_outside and is_claim)
        write_report(out / 'open-reminder-context.csv',
                     [r for r in reminders if r['reminder_task_state'] == 'OPEN'])
        write_report(out / 'returned-to-stock.csv', list(returned_report.values()))
        write_report(out / 'outside-window.csv', list(outside_report.values()))
    summary = {"run_at": datetime.now().isoformat(), "apply": args.apply,
               "claim_window_days": CLAIM_WINDOW_DAYS,
               "start_date": start_date.isoformat(), "monitoring_cutoff": cutoff.isoformat(),
               "missing_events": len(missing),
               "missing_patients": len({e['patient_id'] for e in missing}),
               "late_claim_events": len(late), "new_reminders": len(new),
               "fully_returned_source_events": len(returned_events),
               "fully_returned_open_reminders": sum(e['stock_return_status'] == 'FULLY RETURNED TO STOCK'
                                                    for e in reminders),
               "created_and_verified": len(new) if args.apply else 0,
               "context_updated_and_verified": updated, "context_skipped": skipped}
    summary['completed_returned_reminders'] = completed
    summary['completed_outside_window_reminders'] = completed_outside
    summary['completed_matching_claim_reminders'] = completed_claim
    summary['deferred_and_verified'] = deferred
    summary['waiting_open_reminders'] = sum(r['reminder_task_state'] == 'OPEN' and
        r['claim_window_status'] == 'WAITING FOR FULL WINDOW' for r in reminders)
    summary['due_open_reminders'] = sum(r['reminder_task_state'] == 'OPEN' and
        r['claim_window_status'] == 'WINDOW ELAPSED; NO MATCHING CLAIM' for r in reminders)
    summary.update(cleanup_summary)
    (out / "status.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
