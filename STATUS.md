# Status - October 8, 2026

Nightly biologic missing-claim monitoring is deployed and active at 11:40 p.m.
Eastern on ims-referrals. Scope: all recorded history of the exact target codes.

28 IMS reminders for 20 patients were created and verified, assigned to Ghassan
Safadi and Rachel Clark. Repeat apply created zero duplicates. Ten rule tests
passed. Existing cron entries were preserved and the new entry read back once.

The all-history one-time check found 20 patients / 28 still-missing events,
including 19731. All findings have verified reminders. Fifty other
historical events had late matching claims and are separately reported.

Latest source snapshot: 27 unresolved events for 19 patients. Patient 19731's
June 10 dispense row disappeared during installation; no subsequent J2357 claim
was found. Its original reminder remains for staff review/disposition. Thus 28
verified reminders exist, while 27 source events remain in the latest report.

See docs/biologic-missing-claims-20261008.md for exact semantics, runtime paths,
verification and private report locations. Existing insurance-change monitoring
continues separately under BIO_INS_CHANGE_PA.
