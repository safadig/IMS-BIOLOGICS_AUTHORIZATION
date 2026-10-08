import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from biologic_stock_returns import (enrich_stock, fully_returned_sql, stock_note,
                                    complete_return_sql, complete_window_sql)


def dispense(tran='10', code='XO150', qty='1', adj='0'):
    return {'tran_id': tran, 'patient_id': '5', 'billing_id': code,
            'dispense_date': '2026-06-10', 'item_qty': qty, 'adj_qty': adj,
            'item_id': '100', 'item_lot_no': '3', 'lot_no': 'LOT'}


def returned(tran='10', qty='1', ledger='1', **changes):
    return {'tran_id': '50', 'despense_id': tran, 'patient_id': '5',
            'adjust_for': '1', 'tran_date': '2026-10-07', 'item_id': '100',
            'lot_no': '3', 'item_qty': qty, 'ledger_qty': ledger,
            'office': 'TOL', 'entered_by_name': 'Rachel Clark', **changes}


class StockReturns(unittest.TestCase):
    def check(self, lines, adjustments, **changes):
        event = {'patient_id': '5', 'drug': 'XOLAIR', 'dispense_date': '2026-06-10',
                 'codes': ['XO150'], **changes}
        return enrich_stock([event], lines, adjustments, date(2026, 10, 8))[0]

    def test_exact_two_syringe_return(self):
        e = self.check([dispense(adj='1'), dispense('11', adj='1')],
                       [returned(), returned('11')])
        self.assertEqual(e['stock_return_status'], 'FULLY RETURNED TO STOCK')
        self.assertEqual(e['inventory_returned_qty'], '2')
        self.assertEqual(e['inventory_outstanding_qty'], '0')
        self.assertIn('Rachel Clark', stock_note(e))
        self.assertIn('2026-10-07', stock_note(e))

    def test_partial_return_does_not_clear_event(self):
        e = self.check([dispense(adj='1'), dispense('11')], [returned()])
        self.assertEqual(e['stock_return_status'], 'PARTIALLY RETURNED TO STOCK')
        self.assertEqual(e['inventory_outstanding_qty'], '1')

    def test_fractional_inventory_quantity(self):
        e = self.check([dispense(qty='0.30', adj='0.10')], [returned(qty='0.10', ledger='0.10')])
        self.assertEqual(e['inventory_outstanding_qty'], '0.20')

    def test_unreturned_and_unknown_are_distinct(self):
        self.assertEqual(self.check([dispense()], [])['stock_return_status'], 'NO RECORDED RETURN')
        self.assertEqual(self.check([], [])['stock_return_status'], 'UNKNOWN / REVIEW')

    def test_wrong_patient_item_lot_type_or_date_requires_review(self):
        for changes in [{'patient_id': '6'}, {'item_id': '200'}, {'lot_no': '4'},
                        {'adjust_for': '3'}, {'tran_date': '2026-06-09'},
                        {'tran_date': '2026-10-09'}]:
            self.assertEqual(self.check([dispense(adj='1')], [returned(**changes)])
                             ['stock_return_status'], 'UNKNOWN / REVIEW')

    def test_exact_dispense_link_not_other_return(self):
        self.assertEqual(self.check([dispense()], [returned('99')])['stock_return_status'],
                         'NO RECORDED RETURN')

    def test_ledger_counter_overreturn_and_bad_quantity_require_review(self):
        for line, ret in [(dispense(adj='1'), returned(ledger='0')),
                          (dispense(adj='0'), returned()),
                          (dispense(adj='2'), returned(qty='2', ledger='2')),
                          (dispense(qty='NaN'), returned())]:
            self.assertEqual(self.check([line], [ret])['stock_return_status'], 'UNKNOWN / REVIEW')

    def test_mixed_doses_must_all_be_covered(self):
        e = self.check([dispense(adj='1'), dispense('11', 'XOL75')], [returned()],
                       codes=['XO150', 'XOL75'])
        self.assertEqual(e['stock_return_status'], 'PARTIALLY RETURNED TO STOCK')
        e = self.check([dispense(adj='1')], [returned()], codes=['XO150', 'XOL75'])
        self.assertEqual(e['stock_return_status'], 'UNKNOWN / REVIEW')

    def test_wrong_date_and_drug_inventory_are_excluded(self):
        self.assertEqual(self.check([{**dispense(adj='1'), 'dispense_date': '2026-06-11'}],
                                    [returned()])['stock_return_status'], 'UNKNOWN / REVIEW')
        self.assertEqual(self.check([dispense(code='TEZ', adj='1')], [returned()])
                         ['stock_return_status'], 'UNKNOWN / REVIEW')

    def test_insert_rechecks_all_inventory_lines_and_ledgers(self):
        sql = fully_returned_sql({'patient_id': '5', 'dispense_date': '2026-06-10',
                                  'drug': 'XOLAIR', 'codes': ['XO150', 'XOL75']})
        self.assertIn('NOT EXISTS(SELECT 1 FROM prescription_dispense', sql)
        self.assertIn("il.ref_type='1'", sql)
        self.assertIn('da.despense_id=pd.tran_id', sql)
        self.assertIn('COALESCE(pd.adj_qty,0)<>pd.item_qty', sql)
        self.assertIn("pd.billing_id='XOL75'", sql)

    def test_completion_refuses_partial_unknown_and_unreturned(self):
        for status in ['PARTIALLY RETURNED TO STOCK', 'UNKNOWN / REVIEW', 'NO RECORDED RETURN']:
            with self.assertRaises(ValueError):
                complete_return_sql({'stock_return_status': status}, 'note')

    def test_completion_is_scoped_guarded_and_preserves_done_history(self):
        event = self.check([dispense(adj='1')], [returned()])
        event.update(todo_id='999', source='BIO_NO_CLAIM30:5:J2357:20260610')
        sql = complete_return_sql(event, "Staff's note")
        self.assertIn('UPDATE tobe_done_detail', sql)
        self.assertIn("task_status='D'", sql)
        self.assertIn("(task_status='P' OR task_status IS NULL OR task_status='')", sql)
        self.assertIn("t.source='BIO_NO_CLAIM30:5:J2357:20260610'", sql)
        self.assertIn("COALESCE(t.note,'')='Staff''s note'", sql)
        self.assertIn("il.ref_type='1'", sql)
        self.assertNotIn('DELETE', sql)
        self.assertNotIn('UPDATE todo ', sql)

    def test_window_completion_rejects_cutoff_and_newer(self):
        for day in ['2026-01-08', '2026-06-10']:
            with self.assertRaises(ValueError):
                complete_window_sql({'dispense_date': day}, 'note', date(2026, 1, 8))

    def test_window_completion_uses_separate_reason_and_live_date_guard(self):
        event = {'todo_id': '999', 'patient_id': '5', 'dispense_date': '2026-01-07',
                 'source': 'BIO_NO_CLAIM30:5:J2357:20260107'}
        sql = complete_window_sql(event, 'Unresolved finding preserved', date(2026, 1, 8))
        self.assertIn("RIGHT(t.source,8)<'20260108'", sql)
        self.assertIn("done_by='Biologic monitoring window'", sql)
        self.assertNotIn('Biologic stock return monitor', sql)


if __name__ == '__main__':
    unittest.main()
