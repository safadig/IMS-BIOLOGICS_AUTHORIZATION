import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from biologic_return_visit_cleanup import visit_guard, delete_visit_sql


class ReturnedVisitCleanup(unittest.TestCase):
    def setUp(self):
        self.event = {'patient_id': '5', 'drug': 'XOLAIR', 'dispense_date': '2026-06-10',
                      'codes': ['XO150', 'XOL75']}
        self.visit = {'tran_id': '999', 'ref_id': '77', 'line_count': 1, 'changed_date': None,
                      'lines': [{'sr_id': '2', 'billing_id': 'XO150', 'billing_qty': '60',
                                 'changed_date': '2026-06-10 12:00:00'}]}

    def test_exact_patient_date_visit_and_superbill_identity(self):
        guard = visit_guard(self.event, self.visit)
        for text in ['bh.tran_id=999', 'bh.patient_id=5', "bh.service_from='2026-06-10'",
                     'bh.ref_id=77', 'pd.superbill_pn_id<>bh.ref_id', "bd.billing_id NOT IN ('XO150','XOL75')"]:
            self.assertIn(text, guard)

    def test_nonzero_claim_payment_clinical_or_attached_records_protected(self):
        guard = visit_guard(self.event, self.visit)
        for text in ['bh.total_amt=0', 'COALESCE(bd.total_ins_paid,0)<>0',
                     'COALESCE(bd.pat_open_credit,0)<>0', 'FROM claim_tracker',
                     'FROM receipt_subdetail', 'FROM rejection_hdr',
                     'FROM patient_immunization', 'FROM billing_documents',
                     'x.first_claim_date IS NOT NULL', "x.ref_type='Q'",
                     'COALESCE(bd.pn_id,0)<>0']:
            self.assertIn(text, guard)

    def test_live_return_stock_counter_and_all_lines_are_rechecked(self):
        guard = visit_guard(self.event, self.visit)
        self.assertIn("da.adjust_for='1'", guard)
        self.assertIn('COALESCE(pd.adj_qty,0)<>pd.item_qty', guard)
        self.assertIn('(SELECT COUNT(*) FROM billing_detail bd WHERE bd.tran_id=bh.tran_id)=1', guard)
        self.assertIn('bd.sr_id=2', guard)
        self.assertIn("bd.billing_qty='60'", guard)
        self.assertIn("bd.changed_date='2026-06-10 12:00:00'", guard)

    def test_native_cascade_only_and_audit_failure_rolls_back_before_commit(self):
        sql = delete_visit_sql(self.event, self.visit)
        self.assertEqual(sql.count('DELETE FROM'), 1)
        self.assertIn('DELETE FROM billing_header bh', sql)
        self.assertIn('billing_header_history', sql)
        self.assertIn('billing_detail_history', sql)
        self.assertIn('ROLLBACK;', sql)
        self.assertLess(sql.index('ROLLBACK;'), sql.index('COMMIT;'))
        self.assertNotIn('DELETE FROM prescription_dispense', sql)
        self.assertNotIn('DELETE FROM drug_adjustment', sql)
        self.assertNotIn('DELETE FROM progress_', sql)


if __name__ == '__main__':
    unittest.main()
