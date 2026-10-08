"""Remove only unclaimed zero-dollar visits for verified fully returned biologics."""
from datetime import date, datetime
import csv
import json
from pathlib import Path
import tempfile

from biologic_followup import q, xml_select
from biologic_stock_returns import CODES, query_stock, enrich_stock, fully_returned_sql

MONEY = ['billing_amt', 'amount', 'total_ins_paid', 'total_ins_writeoff',
         'total_patient_paid', 'total_patient_writeoff', 'ins_balance', 'pat_balance',
         'pat_open_credit', 'transfer_amt']
PROTECTED = {'claim_tracker': 'tran_id', 'receipt_subdetail': 'srv_tran_id',
             'rejection_hdr': 'bill_tran_id', 'patient_immunization': 'bill_tran_id',
             'billing_immunization': 'tran_id', 'billing_documents': 'tran_id',
             'case_bill_detail': 'tran_id'}
SNAPSHOT = {'billing_header': 'tran_id', 'billing_detail': 'tran_id',
            'billing_subdetail': 'tran_id', 'billing_header_moreinfo': 'tran_id',
            'bill_condition': 'tran_id', 'bill_diagnosis': 'tran_id',
            'bill_occurrence': 'tran_id', 'bill_occurrence_span': 'tran_id',
            'bill_procedure_ub92': 'tran_id', 'bill_value': 'tran_id',
            **PROTECTED}


def returned_inventory_events(select):
    codes = ','.join(q(c) for c in CODES)
    patients = xml_select(select, f"""SELECT DISTINCT pd.patient_id
FROM prescription_dispense pd JOIN drug_adjustment da ON da.despense_id=pd.tran_id
WHERE pd.billing_id IN ({codes}) AND da.adjust_for='1' AND da.tran_date<=TODAY()""")
    dispenses, returns = query_stock(select, [p['patient_id'] for p in patients])
    linked = {r['despense_id'] for r in returns}
    events = {}
    for d in dispenses:
        if d['tran_id'] in linked:
            key = (d['patient_id'], CODES[d['billing_id']], d['dispense_date'])
            events.setdefault(key, {'patient_id': key[0], 'drug': key[1], 'dispense_date': key[2]})
    for event in events.values():
        event['codes'] = sorted({d['billing_id'] for d in dispenses
                                if d['patient_id'] == event['patient_id']
                                and d['dispense_date'] == event['dispense_date']
                                and CODES[d['billing_id']] == event['drug']})
    enrich_stock(list(events.values()), dispenses, returns, date.today())
    return list(events.values()), dispenses, returns


def visit_guard(event, visit):
    codes = ','.join(q(c) for c in event['codes'])
    day = q(event['dispense_date'])
    conditions = [f"bh.tran_id={int(visit['tran_id'])}",
                  f"bh.patient_id={int(event['patient_id'])}", f"bh.service_from={day}",
                  'bh.total_amt=0', "bh.form_flag='H'", "bh.entry_from IN ('Q','P')",
                  f"bh.ref_id={int(visit['ref_id'])}",
                  'COALESCE(bh.case_autho_id,0)=0', 'COALESCE(bh.cycle_id,0)=0',
                  'COALESCE(bh.ip_cycle_id,0)=0', fully_returned_sql(event),
                  f"(SELECT COUNT(*) FROM billing_detail bd WHERE bd.tran_id=bh.tran_id)={visit['line_count']}"]
    changed = visit.get('changed_date')
    conditions.append(f"bh.changed_date={q(changed)}" if changed else 'bh.changed_date IS NULL')
    bad_money = ' OR '.join(f"COALESCE(bd.{f},0)<>0" for f in MONEY)
    conditions.append(f"""NOT EXISTS(SELECT 1 FROM billing_detail bd WHERE bd.tran_id=bh.tran_id
 AND (bd.billing_id IS NULL OR bd.billing_id NOT IN ({codes})
 OR bd.service_date IS NULL OR bd.service_date<>{day}
 OR bd.billing_amt IS NULL OR bd.amount IS NULL OR {bad_money}
 OR COALESCE(bd.billing_qty,0)<=0 OR COALESCE(bd.pn_id,0)<>0
 OR COALESCE(bd.patient_package_id,0)<>0
 OR COALESCE(bd.entry_from,'') NOT IN ('Q','P')))""")
    # Direct superbill reference prevents matching a separate same-day visit.
    conditions.append(f"""NOT EXISTS(SELECT 1 FROM prescription_dispense pd
 WHERE pd.patient_id=bh.patient_id AND pd.dispense_date={day} AND pd.billing_id IN ({codes})
 AND (pd.superbill_pn_id IS NULL OR pd.superbill_pn_id<>bh.ref_id))""")
    for table, column in PROTECTED.items():
        conditions.append(f'NOT EXISTS(SELECT 1 FROM {table} x WHERE x.{column}=bh.tran_id)')
    conditions.extend([
        """NOT EXISTS(SELECT 1 FROM billing_subdetail x WHERE x.tran_id=bh.tran_id
 AND (COALESCE(x.last_claim_id,0)<>0 OR x.last_claim_date IS NOT NULL
 OR x.first_claim_date IS NOT NULL OR COALESCE(x.export_last_claim_id,0)<>0
 OR COALESCE(x.paid_amt,0)<>0 OR COALESCE(x.adjusted_amt,0)<>0
 OR COALESCE(x.patresp_amt,0)<>0))""",
        "NOT EXISTS(SELECT 1 FROM item_ledger x WHERE x.ref_type='Q' AND x.ref_tran_id=bh.tran_id)",
        """NOT EXISTS(SELECT 1 FROM billing_aging x WHERE x.tran_id=bh.tran_id
 AND (COALESCE(x.amount,0)<>0 OR COALESCE(x.amt_adjusted,0)<>0 OR COALESCE(x.receipt_id,0)<>0))""",
    ])
    # Preserve exact detail quantity and update timestamps seen during review.
    for line in visit['lines']:
        stamp = (f"bd.changed_date={q(line['changed_date'])}" if line.get('changed_date')
                 else 'bd.changed_date IS NULL')
        conditions.append(f"""EXISTS(SELECT 1 FROM billing_detail bd WHERE bd.tran_id=bh.tran_id
 AND bd.sr_id={int(line['sr_id'])} AND bd.billing_id={q(line['billing_id'])}
 AND bd.billing_qty={q(line['billing_qty'])} AND {stamp})""")
    return '\n AND '.join(conditions)


def delete_visit_sql(event, visit):
    sr_ids = ','.join(str(int(d['sr_id'])) for d in visit['lines'])
    return f"""
SET TEMPORARY OPTION auto_commit='Off';
SET TEMPORARY OPTION blocking_timeout='5000';
BEGIN
DECLARE deleted_count INTEGER;
DELETE FROM billing_header bh WHERE {visit_guard(event, visit)};
SET deleted_count=@@ROWCOUNT;
IF deleted_count=1 THEN
 IF EXISTS(SELECT 1 FROM billing_detail WHERE tran_id={int(visit['tran_id'])})
 OR NOT EXISTS(SELECT 1 FROM billing_header_history WHERE tran_id={int(visit['tran_id'])} AND action_type=3)
 OR (SELECT COUNT(DISTINCT sr_id) FROM billing_detail_history
 WHERE tran_id={int(visit['tran_id'])} AND action_type=3 AND sr_id IN ({sr_ids}))<>{visit['line_count']}
 OR NOT {fully_returned_sql(event)} THEN
  ROLLBACK;
  RAISERROR 99999 'Returned dispense cleanup audit or inventory validation failed';
 END IF;
END IF;
COMMIT;
END;
"""


def snapshot(select, events, visits, dispenses, returns):
    ids = ','.join(str(int(v['tran_id'])) for v in visits)
    data = {'events': events, 'visits': visits, 'dispenses': dispenses, 'returns': returns}
    if ids:
        for table, column in SNAPSHOT.items():
            data[table] = xml_select(select, f'SELECT * FROM {table} WHERE {column} IN ({ids})')
    inv = ','.join(d['tran_id'] for d in dispenses)
    adj = ','.join(r['tran_id'] for r in returns)
    if inv:
        data['inventory_native'] = xml_select(select, f'SELECT * FROM prescription_dispense WHERE tran_id IN ({inv}) ORDER BY tran_id')
    if adj:
        data['return_native'] = xml_select(select, f'SELECT * FROM drug_adjustment WHERE tran_id IN ({adj}) ORDER BY tran_id')
    if inv or adj:
        data['inventory_ledger_native'] = xml_select(select, f"""SELECT * FROM item_ledger
 WHERE (ref_type='D' AND ref_tran_id IN ({inv or 'NULL'}))
 OR (ref_type='1' AND ref_tran_id IN ({adj or 'NULL'})) ORDER BY tran_id""")
    return data


def cleanup_return_visits(select, execute, output, apply=False):
    # The nine-month limit applies to open reminders, not returned-charge cleanup.
    events, dispenses, returns = returned_inventory_events(select)
    full = [e for e in events if e['stock_return_status'] == 'FULLY RETURNED TO STOCK']
    records, visits = [], []
    if full:
        pairs = ' OR '.join(f"(bh.patient_id={int(e['patient_id'])} AND bd.service_date={q(e['dispense_date'])} "
                           f"AND bd.billing_id IN ({','.join(q(c) for c in e['codes'])}))" for e in full)
        headers = xml_select(select, f"""SELECT DISTINCT bh.*,pm.patient_no
 FROM billing_header bh JOIN billing_detail bd ON bd.tran_id=bh.tran_id
 JOIN patient_master pm ON pm.id=bh.patient_id WHERE {pairs}""")
        if headers:
            ids = ','.join(h['tran_id'] for h in headers)
            lines = xml_select(select, f'SELECT * FROM billing_detail WHERE tran_id IN ({ids}) ORDER BY tran_id,sr_id')
            for h in headers:
                h['lines'] = [d for d in lines if d['tran_id'] == h['tran_id']]
                h['line_count'] = len(h['lines'])
                event = next(e for e in full if e['patient_id'] == h['patient_id']
                             and any(d['service_date'] == e['dispense_date'] and d['billing_id'] in e['codes']
                                     for d in h['lines']))
                # Missing reference is reviewable; never build a delete for it.
                eligible = False
                if h['ref_id'] and int(h['ref_id']) > 0 and h['line_count']:
                    eligible = bool(xml_select(select, f"SELECT bh.tran_id FROM billing_header bh WHERE {visit_guard(event,h)}"))
                record = {'patient_no': h['patient_no'], 'patient_id': h['patient_id'],
                          'drug': event['drug'], 'dispense_date': event['dispense_date'],
                          'billing_tran_id': h['tran_id'], 'stock_return_status': event['stock_return_status'],
                          'return_details': event['stock_return_details'],
                          'result': 'ELIGIBLE' if eligible else 'PRESERVED / REVIEW'}
                records.append(record)
                if eligible:
                    visits.append((event, h, record))
    deleted = 0
    if apply and visits:
        evidence = Path(output) / 'visit-cleanup-evidence'
        evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%dT%H%M%S%f')
        selected = [e for e, _, _ in visits]
        keys = {(e['patient_id'], e['drug'], e['dispense_date']) for e in selected}
        selected_dispenses = [d for d in dispenses
                             if (d['patient_id'], CODES[d['billing_id']], d['dispense_date']) in keys]
        inventory_ids = {d['tran_id'] for d in selected_dispenses}
        selected_returns = [r for r in returns if r['despense_id'] in inventory_ids]
        before = snapshot(select, selected, [v for _, v, _ in visits], selected_dispenses, selected_returns)
        backup = evidence / (stamp + '-before.json')
        with backup.open('x', encoding='utf-8') as handle:
            json.dump(before, handle)
        backup.chmod(0o600)
        for event, visit, record in visits:
            with tempfile.NamedTemporaryFile('w', suffix='.sql', delete=False) as handle:
                handle.write(delete_visit_sql(event, visit))
                script = Path(handle.name)
            try:
                result = execute(str(script))
                if result.returncode:
                    raise RuntimeError('Returned dispense visit deletion failed; inspect private backup')
            finally:
                script.unlink(missing_ok=True)
            remaining = xml_select(select, f"SELECT tran_id FROM billing_header WHERE tran_id={int(visit['tran_id'])}")
            if remaining:
                record['result'] = 'PRESERVED / CHANGED DURING APPLY'
                continue
            header_history = xml_select(select, f"SELECT tran_id FROM billing_header_history WHERE tran_id={int(visit['tran_id'])} AND action_type=3")
            detail_history = xml_select(select, f"SELECT sr_id FROM billing_detail_history WHERE tran_id={int(visit['tran_id'])} AND action_type=3")
            detail_remaining = xml_select(select, f"SELECT sr_id FROM billing_detail WHERE tran_id={int(visit['tran_id'])}")
            if not header_history or {r['sr_id'] for r in detail_history} != {r['sr_id'] for r in visit['lines']} or detail_remaining:
                raise RuntimeError('Native dispense deletion audit/readback failed')
            record['result'] = 'DELETED / NATIVE AUDIT VERIFIED'
            deleted += 1
            with (evidence / 'deletions.jsonl').open('a', encoding='utf-8') as handle:
                handle.write(json.dumps({'at': datetime.now().isoformat(), 'backup': backup.name, **record}) + '\n')
        after = snapshot(select, selected, [v for _, v, _ in visits], selected_dispenses, selected_returns)
        after_path = evidence / (stamp + '-after.json')
        after_path.write_text(json.dumps(after), encoding='utf-8')
        after_path.chmod(0o600)
        for key in ['inventory_native', 'return_native', 'inventory_ledger_native']:
            if before.get(key) != after.get(key):
                raise RuntimeError('Inventory records changed during billing cleanup; inspect private evidence')
    report = Path(output) / 'returned-visit-cleanup.csv'
    with report.open('w', newline='', encoding='utf-8-sig') as handle:
        fields = ['patient_no', 'drug', 'dispense_date', 'billing_tran_id',
                  'stock_return_status', 'return_details', 'result']
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(records)
    report.chmod(0o600)
    return {'verified_full_return_events': len(full),
            'partial_or_uncertain_return_events': len(events) - len(full),
            'remaining_returned_visits_reviewed': len(records),
            'returned_zero_visits_deleted': deleted,
            'returned_visits_preserved': sum(r['result'].startswith('PRESERVED') for r in records)}
