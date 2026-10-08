#!/usr/bin/env python3
"""Nightly IMS dispense-to-claim check; reports are private, logs are aggregate."""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

DRUGS = {
    "XOL75": ("XOLAIR", "J2357", 14),
    "XO150": ("XOLAIR", "J2357", 14),
    "XO300": ("XOLAIR", "J2357", 14),
    "FAS30": ("FASENRA", "J0517", 81),
    "FASDI": ("FASENRA", "J0517", 81),
    "TEZ": ("TEZSPIRE", "J2356", 123),
}
SOURCE = "BIO_NO_CLAIM30"


def sql_string(value):
    return "'" + str(value).replace("'", "''") + "'"


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
        deadline = day + timedelta(days=30)
        if today <= deadline:  # Give the complete day-30 service window.
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


def write_report(path, events):
    fields = ["patient_no", "patient_name", "drug", "codes", "dispense_date",
              "deadline", "days_since_dispense", "j_code", "later_claim_date",
              "later_posted_j_date", "source"]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for event in events:
            writer.writerow({**event, "codes": "/".join(event["codes"])})
    path.chmod(0o600)


def insert_sql(event):
    q = sql_string
    note = (f"{event['drug']} dispensed {event['dispense_date']} "
            f"({'/'.join(event['codes'])}). No subsequent {event['j_code']} claim "
            "is recorded after the full 30-day window. Review administration, "
            "billing/claim submission, supply source, or documented disposition. "
            f"Day-30 deadline: {event['deadline']}. "
            "A billing line without a linked claim does not satisfy this check.")
    if event["later_posted_j_date"]:
        note += f" A J-code charge exists dated {event['later_posted_j_date']}; review claim linkage."
    # Both inserts commit together. Use the native detail autoincrement default
    # instead of MAX+1 or a table lock; dedupe includes staff-completed rows.
    return f"""
SET TEMPORARY OPTION auto_commit='Off';
SET TEMPORARY OPTION blocking_timeout='5000';
INSERT INTO todo (
 tran_id,tran_date,status,forwhom,forwhom_name,category_id,todo,todo_at,
 todo_date,todo_time,tobe_doneby,generated_by,priority,todo_by,todo_by_id,
 todo_by_multi_id,todo_by_multi_group,assignto_flag,forwhom_id,ref_tran_id,
 ref_sr_id,note,source,is_auto,created_date,changed_date,createdby_id,changedby_id)
SELECT seq_todo_id.NEXTVAL,TODAY(),'G','P',{q(event['patient_name'])},
 {event['category_id']},'Biologic dispensed - no J-code claim after 30 days','T',
 TODAY(),CURRENT TIME,'Safadi, Ghassan * Clark, Rachel','U','H','U',NULL,
 ',1,24,',NULL,'N',{int(event['patient_id'])},{int(event['tran_id'])},
 {int(event['sr_id'])},{q(note)},{q(event['source'])},'N',
 CURRENT TIMESTAMP,CURRENT TIMESTAMP,-1,-1
WHERE NOT EXISTS(SELECT 1 FROM todo WHERE source={q(event['source'])})
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
    parser.add_argument("--start-date", default="2025-10-08",
                        help="Fixed initial backlog boundary; does not roll forward")
    parser.add_argument("--output-dir", default="/opt/ims_router/output/biologic_missing_claims")
    args = parser.parse_args()
    os.umask(0o077)
    today = date.today()
    missing, late = evaluate(query_rows(), today, date.fromisoformat(args.start_date))
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_report(out / "missing-current.csv", missing)
    write_report(out / "late-claims.csv", late)
    select, execute = db_helpers()
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
    summary = {"run_at": datetime.now().isoformat(), "apply": args.apply,
               "start_date": args.start_date, "missing_events": len(missing),
               "missing_patients": len({e['patient_id'] for e in missing}),
               "late_claim_events": len(late), "new_reminders": len(new),
               "created_and_verified": len(new) if args.apply else 0}
    (out / "status.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
