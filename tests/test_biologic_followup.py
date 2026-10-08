import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import biologic_followup as f


def event():
    return {'patient_id': '1', 'drug': 'XOLAIR', 'j_code': 'J2357', 'dispense_date': '2026-09-01'}


def appt(day, status='D', procedures=None, canceled='', patient='1'):
    return {'patient_id': patient, 'appointment_id': '10', 'date': day, 'time': '10:00:00',
            'status': status, 'canceled': canceled, 'office': 'TOL',
            'procedures': procedures or ['21', '', ''], 'description': 'XOLAIR'}


class Followup(unittest.TestCase):
    def test_missed_is_after_dispense_same_drug_patient(self):
        e = event()
        rows = [appt('2026-08-30', 'M'), appt('2026-09-05', 'M'),
                appt('2026-09-06', 'M', ['53', '', '']), appt('2026-09-07', 'M', patient='2')]
        f.enrich([e], rows, [], date(2026, 10, 8))
        self.assertEqual(e['missed_biologic_count'], 1)
        self.assertIn('2026-09-05', e['missed_biologic_details'])

    def test_secondary_and_tertiary_slots(self):
        for slots in [['1', '21', ''], ['1', '', '21']]:
            e = event()
            f.enrich([e], [appt('2026-09-02', 'M', slots)], [], date(2026, 10, 8))
            self.assertEqual(e['missed_biologic_count'], 1)

    def test_future_d_is_scheduled_not_completed_and_cancel_excluded(self):
        e = event()
        rows = [appt('2026-10-10', canceled='2026-10-01'), appt('2026-10-11', 'C'),
                appt('2026-10-12', 'D'), appt('2026-10-13', 'M')]
        f.enrich([e], rows, [], date(2026, 10, 8))
        self.assertIn('2026-10-12', e['next_biologic_appointment'])
        self.assertEqual(e['missed_biologic_count'], 0)

    def test_next_any_can_differ_from_matching_biologic(self):
        e = event()
        rows = [appt('2026-10-09', procedures=['1', '', '']), appt('2026-10-15')]
        rows[0]['description'] = 'Follow-up'
        f.enrich([e], rows, [], date(2026, 10, 8))
        self.assertIn('2026-10-09', e['next_any_appointment'])
        self.assertIn('2026-10-15', e['next_biologic_appointment'])

    def test_service_source_same_drug_and_tied_offices(self):
        e = event()
        rows = [{'patient_id': '1', 'date': '2026-08-01', 'j_code': 'J2357', 'office': office,
                 'tran_id': str(i), 'sr_id': '1'} for i, office in enumerate(['TOL', 'FRE'], 1)]
        rows.append({**rows[0], 'date': '2026-09-01', 'j_code': 'J2356'})
        f.enrich([e], [], rows, date(2026, 10, 8))
        self.assertEqual(e['last_biologic_service_date'], '2026-08-01')
        self.assertEqual(e['last_biologic_service_office'], 'FRE/TOL')

    def test_missing_office_is_unknown(self):
        e = event()
        f.enrich([e], [], [], date(2026, 10, 8))
        self.assertEqual(e['last_biologic_service_office'], 'UNKNOWN')

    def test_managed_note_preserves_staff_text_and_repeat_is_identical(self):
        old = 'Staff note: C:\\patient\\record\nDo not lose this. '
        merged = f.merge_note(old, 'context one')
        self.assertTrue(merged.startswith(old))
        self.assertEqual(f.merge_note(merged, 'context one'), merged)
        updated = f.merge_note(merged + '\nStaff suffix', 'context two')
        self.assertTrue(updated.startswith(old))
        self.assertTrue(updated.endswith('\nStaff suffix'))

    def test_oversize_or_malformed_note_is_preserved(self):
        self.assertIsNone(f.merge_note('x' * 2000, 'new'))
        self.assertIsNone(f.merge_note(f.START + 'no end', 'new'))

    def test_refresh_guards_exact_note_and_done_without_rerouting(self):
        sql = f.refresh_sql({'todo_id': '1', 'source': 'BIO_NO_CLAIM30:1:J2357:20260901',
                            'old_note': 'staff'}, 'new')
        self.assertIn("COALESCE(note,'')='staff'", sql)
        self.assertIn("d.task_status='P'", sql)
        self.assertNotIn('todo_by_multi_id=', sql)
        self.assertNotIn('UPDATE tobe_done_detail', sql)


if __name__ == '__main__':
    unittest.main()
