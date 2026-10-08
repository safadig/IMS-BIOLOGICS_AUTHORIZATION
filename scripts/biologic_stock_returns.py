"""Verify returns against the exact IMS inventory dispense and stock ledger."""
from decimal import Decimal, InvalidOperation

from biologic_followup import q, xml_select

CODES = {"XOL75": "XOLAIR", "XO150": "XOLAIR", "XO300": "XOLAIR",
         "FAS30": "FASENRA", "FASDI": "FASENRA", "TEZ": "TEZSPIRE"}
FIELDS = ["stock_return_status", "inventory_dispense_ids", "inventory_dispensed_qty",
          "inventory_returned_qty", "inventory_outstanding_qty", "stock_return_details",
          "stock_return_basis"]


def number(value):
    try:
        result = Decimal(value or "0")
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError):
        return None


def ledger_sum(alias="da"):
    # ref_type 1 is the native dispense-return adjustment, linked to its ID.
    return f"""(SELECT COALESCE(SUM(il.item_qty),0) FROM item_ledger il
 WHERE il.ref_type='1' AND il.ref_tran_id={alias}.tran_id
 AND il.patient_id={alias}.patient_id AND il.item_id={alias}.item_id
 AND il.lot_no={alias}.lot_no AND il.office_id={alias}.office_id)"""


def query_stock(select, patient_ids):
    ids = sorted({int(p) for p in patient_ids})
    if not ids:
        return [], []
    scope = ','.join(map(str, ids))
    codes = ','.join(q(c) for c in CODES)
    dispenses = xml_select(select, f"""
SELECT pd.tran_id,pd.patient_id,pd.billing_id,pd.dispense_date,pd.item_id,
 pd.item_lot_no,pd.item_qty,pd.adj_qty,pd.office_id,pd.lot_no,pd.superbill_pn_id,pd.billing_unit,
 COALESCE(om.office_code,'UNKNOWN') AS office
FROM prescription_dispense pd LEFT JOIN office_master om ON om.srno=pd.office_id
WHERE pd.patient_id IN ({scope}) AND pd.billing_id IN ({codes})
 AND pd.dispense_date<=TODAY()
""")
    returns = xml_select(select, f"""
SELECT da.tran_id,da.despense_id,da.patient_id,da.adjust_for,da.tran_date,
 da.item_id,da.lot_no,da.office_id,da.item_qty,da.entered_by,
 COALESCE(em.firstname,'') || ' ' || COALESCE(em.lastname,'') AS entered_by_name,
 COALESCE(om.office_code,'UNKNOWN') AS office,da.note,da.reason,
 {ledger_sum()} AS ledger_qty
FROM drug_adjustment da JOIN prescription_dispense pd ON pd.tran_id=da.despense_id
LEFT JOIN emp_master em ON em.empid=da.entered_by
LEFT JOIN office_master om ON om.srno=da.office_id
WHERE pd.patient_id IN ({scope}) AND pd.billing_id IN ({codes})
 AND da.tran_date<=TODAY()
""")
    return dispenses, returns


def enrich_stock(events, dispenses, returns, today):
    for event in events:
        lines = [d for d in dispenses if d['patient_id'] == event['patient_id']
                 and d['dispense_date'] == event['dispense_date']
                 and CODES.get(d['billing_id']) == event['drug']]
        issues, details = [], []
        total, returned = Decimal(0), Decimal(0)
        if not lines:
            issues.append('No matching inventory dispense record')
        expected_codes = set(event.get('codes', []))
        if expected_codes - {d['billing_id'] for d in lines}:
            issues.append('Inventory records do not cover every billed dispense code')
        for line in lines:
            qty, adj = number(line['item_qty']), number(line['adj_qty'])
            linked = [r for r in returns if r['despense_id'] == line['tran_id']]
            valid = []
            for r in linked:
                amount, ledger = number(r['item_qty']), number(r['ledger_qty'])
                identity = (r['patient_id'] == line['patient_id']
                            and r['item_id'] == line['item_id']
                            and r['lot_no'] == line['item_lot_no'])
                if (r['adjust_for'] != '1' or not identity or amount is None or amount <= 0
                        or ledger != amount or not r['tran_date']
                        or not line['dispense_date'] <= r['tran_date'] <= today.isoformat()):
                    issues.append(f"Unverified return adjustment {r['tran_id']}")
                    continue
                valid.append(r)
                details.append(f"{r['tran_date']} {r['office']} {r['entered_by_name'].strip() or 'Unknown staff'}: "
                               f"{amount} inventory unit(s), dispense {line['tran_id']}, "
                               f"return {r['tran_id']}, lot {line['lot_no'] or line['item_lot_no']}")
            line_returned = sum((number(r['item_qty']) for r in valid), Decimal(0))
            if qty is None or qty <= 0 or adj is None or adj != line_returned or line_returned > qty:
                issues.append(f"Inventory quantity mismatch for dispense {line['tran_id']}")
            if qty is not None and qty > 0:
                total += qty
            returned += line_returned
        status = ('UNKNOWN / REVIEW' if issues else 'FULLY RETURNED TO STOCK'
                  if returned == total else 'PARTIALLY RETURNED TO STOCK'
                  if returned > 0 else 'NO RECORDED RETURN')
        event.update({
            'stock_return_status': status,
            'inventory_dispense_ids': '/'.join(d['tran_id'] for d in lines),
            'inventory_dispensed_qty': str(total) if lines else '',
            'inventory_returned_qty': str(returned) if lines else '',
            'inventory_outstanding_qty': str(total - returned) if lines and not issues else '',
            'stock_return_details': '; '.join(details),
            'stock_return_basis': '; '.join(sorted(set(issues))) if issues else
                'Matching inventory dispense with no linked return adjustment or adjusted quantity'
                if status == 'NO RECORDED RETURN' else
                'Exact dispense-linked return adjustment, native adjusted quantity and matching stock ledger',
        })
    return events


def stock_note(event):
    status = event.get('stock_return_status', 'UNKNOWN / REVIEW')
    qty = (f"{event.get('inventory_returned_qty') or '?'} of "
           f"{event.get('inventory_dispensed_qty') or '?'} inventory units returned")
    details = event.get('stock_return_details') or event.get('stock_return_basis', '')
    if len(details) > 350:
        details = details[:320].rsplit(';', 1)[0] + '; more in return report'
    disposition = ('Full return accounts for the dispense.'
                   if status == 'FULLY RETURNED TO STOCK' else
                   'Review the remaining quantity.' if status == 'PARTIALLY RETURNED TO STOCK' else '')
    return f"Stock return: {status}; {qty}. {details}. {disposition}".strip()


def fully_returned_sql(event):
    """Recheck returns in the insert statement, so a newly completed return suppresses it."""
    codes = ','.join(q(c) for c, drug in CODES.items() if drug == event['drug'])
    scope = (f"pd.patient_id={int(event['patient_id'])} "
             f"AND pd.dispense_date={q(event['dispense_date'])} AND pd.billing_id IN ({codes})")
    coverage = ''.join(f" AND EXISTS(SELECT 1 FROM prescription_dispense pd WHERE {scope} "
                       f"AND pd.billing_id={q(code)})" for code in event.get('codes', []))
    valid_returns = f"""(SELECT COALESCE(SUM(da.item_qty),0) FROM drug_adjustment da
 WHERE da.despense_id=pd.tran_id AND da.adjust_for='1'
 AND da.patient_id=pd.patient_id AND da.item_id=pd.item_id AND da.lot_no=pd.item_lot_no
 AND da.tran_date BETWEEN pd.dispense_date AND TODAY()
 AND da.item_qty>0 AND {ledger_sum()}=da.item_qty)"""
    # Explicitly handle null/zero quantities; never let SQL UNKNOWN imply returned.
    return f"""(EXISTS(SELECT 1 FROM prescription_dispense pd WHERE {scope}){coverage}
 AND NOT EXISTS(SELECT 1 FROM prescription_dispense pd WHERE {scope}
 AND (COALESCE(pd.item_qty,0)<=0 OR COALESCE(pd.adj_qty,0)<>pd.item_qty
 OR {valid_returns}<>pd.item_qty
 OR EXISTS(SELECT 1 FROM drug_adjustment bad WHERE bad.despense_id=pd.tran_id
 AND (COALESCE(bad.adjust_for,'')<>'1' OR bad.patient_id IS NULL
 OR bad.patient_id<>pd.patient_id OR bad.item_id IS NULL OR bad.item_id<>pd.item_id
 OR bad.lot_no IS NULL OR bad.lot_no<>pd.item_lot_no
 OR bad.tran_date IS NULL OR bad.tran_date<pd.dispense_date OR bad.tran_date>TODAY()
 OR COALESCE(bad.item_qty,0)<=0 OR {ledger_sum('bad')}<>bad.item_qty)))))"""


def complete_return_sql(reminder, expected_note):
    """User-authorized completion; retain parent and source key like native My Tasks."""
    if reminder.get('stock_return_status') != 'FULLY RETURNED TO STOCK':
        raise ValueError('Only verified full returns may complete a reminder')
    return completion_sql(reminder, expected_note, fully_returned_sql(reminder),
                          'Biologic stock return monitor')


def complete_window_sql(reminder, expected_note, cutoff):
    if reminder['dispense_date'] >= cutoff.isoformat():
        raise ValueError('Dispense is inside the monitoring window')
    return completion_sql(reminder, expected_note,
                          f"RIGHT(t.source,8)<{q(cutoff.strftime('%Y%m%d'))}",
                          'Biologic monitoring window')


def completion_sql(reminder, expected_note, predicate, actor):
    return f"""
SET TEMPORARY OPTION auto_commit='Off';
SET TEMPORARY OPTION blocking_timeout='5000';
UPDATE tobe_done_detail SET task_status='D',done_date=TODAY(),done_time=CURRENT TIME,
 done_by={q(actor)},done_by_id=-1,changed_date=CURRENT TIMESTAMP,
 changedby_id=-1,changed_by=-1
WHERE todo_id={int(reminder['todo_id'])}
 AND (task_status='P' OR task_status IS NULL OR task_status='')
 AND EXISTS(SELECT 1 FROM todo t WHERE t.tran_id=tobe_done_detail.todo_id
 AND t.status='G' AND t.source={q(reminder['source'])}
 AND t.source LIKE 'BIO_NO_CLAIM30:%' AND t.forwhom_id={int(reminder['patient_id'])}
 AND COALESCE(t.note,'')={q(expected_note)} AND {predicate});
COMMIT;
"""
