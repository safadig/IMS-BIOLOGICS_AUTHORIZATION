# IMS Biologics Authorization Guard

This workspace contains a first-pass IMS audit for a biologics safety problem:

When a patient is scheduled to receive Xolair, Fasenra, or Tezspire, flag cases where the patient's current primary insurance appears to have changed after the most recent drug dispense charge. Those patients need PA/no-PA verification before administration.

## What I Found

- The local Windows ODBC DSN `meditab2` is not configured on this desktop.
- The existing `ims-referrals` SSH runtime does have IMS SQL Anywhere access and can run schema/report queries.
- IMS already has a `z_biologic_schedule_14d` table, but it is stale right now. On 2026-05-08 it only contained 2026-03-10 through 2026-03-24.
- Live biologic appointment detection should use `schedule_detail.procedure_id`:
  - `21` = Xolair
  - `53` = Fasenra
  - `55` = Tezspire
  - `51` = Dupixent, not included in the initial PA-risk query
- Patient linkage for charges is:
  - `billing_detail.tran_id`
  - `billing_header.tran_id`
  - `billing_header.patient_id`
- Current primary insurance is `patient_insurance.priority = 'P'` and `active = 'Y'`.

## Files

- `sql/biologic_insurance_change_alerts.sql`
  - Main SQL Anywhere query for upcoming biologic appointments.
  - Detects current primary plan/member/group changes compared with the primary insurance active on the last dispense date.
  - Also flags lower-confidence cases where the current primary row was edited after the dispense.

- `sql/biologic_insurance_change_reminder_candidates.sql`
  - Finds patients with a Xolair/Fasenra/Tezspire dispense in the last 45 days and a recent primary insurance change.
  - Maps the reminder category to the biologic and routes to the `Biologics` reminder group.

- `sql/create_biologic_insurance_change_reminders.sql`
  - Inserts IMS `todo` reminders for the candidate rows.
  - Suppresses duplicate open reminders using `source = 'BIO_INS_CHANGE_PA'` and the dispense transaction.

- `scripts/run-biologic-insurance-alerts.ps1`
  - Runs the SQL through the existing `ims-referrals` runtime.
  - Saves a timestamped CSV report under `reports/`.

- `scripts/run-biologic-insurance-change-reminders.ps1`
  - Dry-run by default.
  - Use `-Apply` only after reviewing the candidate CSV.

## Run

```powershell
.\scripts\run-biologic-insurance-alerts.ps1
```

The report intentionally does not write anything back into IMS. It is safe for review/audit use first.

The generated `reports/` folder is ignored by git because it contains patient-level details.

For the insurance-change reminder workflow:

```powershell
.\scripts\run-biologic-insurance-change-reminders.ps1
```

After review:

```powershell
.\scripts\run-biologic-insurance-change-reminders.ps1 -Apply
```

Defaults:

- dispense lookback: `45` days
- insurance-change lookback: `3` days
- reminder group: `Biologics`, `todo_group.group_id = 14`
- reminder source marker: `BIO_INS_CHANGE_PA`
- task heading: `ALERT! Biologic Dispensed and Insurance changed`
- patient-specific detail: stored in the reminder note
- duplicate rule: one open automation reminder per patient, based on the most recent biologic dispense
- insurance-change rule: the current primary insurance must have started or been created after the biologic dispense; a plain `changed_date` edit is not enough

## Validation On 2026-05-08

The first live run returned `69` review rows for appointments from 2026-05-08 through 2026-05-22:

- `11` high-confidence rows where the primary plan changed.
- `34` rows where the current primary insurance row was edited after the last dispense.
- `24` rows where no recent matching dispense was found by the current dispense-code map.

The first reminder dry-run returned `11` candidate rows. The insert SQL was syntax-checked with a no-op condition and inserted `0` rows as expected.

## Alert Workflow

Use the query output to create an operational queue for the biologics team:

- High priority:
  - primary plan changed
  - member ID changed
  - subscriber/insurance number changed
  - group number changed
  - no current primary insurance
- Review priority:
  - current primary insurance row edited after dispense
  - no primary insurance row found at dispense date
  - no recent dispense found

Recommended statuses for the team:

- Open
- PA required - pending
- PA approved
- No PA required for new plan
- Hold biologic
- Insurance corrected
- Resolved
