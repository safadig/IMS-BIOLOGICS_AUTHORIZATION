# Status - October 8, 2026

Nightly biologic missing-claim monitoring is deployed and active at 11:40 p.m.
Eastern on ims-referrals. Initial fixed dispense boundary: October 8, 2025.

24 IMS reminders for 16 patients were created and verified, assigned to Ghassan
Safadi and Rachel Clark. Repeat apply created zero duplicates. Ten rule tests
passed. Existing cron entries were preserved and the new entry read back once.

The all-history one-time check found 20 patients / 28 still-missing events,
including 19731. Four older patient findings remain report-only. Fifty other
historical events had late matching claims and are separately reported.

See docs/biologic-missing-claims-20261008.md for exact semantics, runtime paths,
verification and private report locations. Existing insurance-change monitoring
continues separately under BIO_INS_CHANGE_PA.
