# Implementation Notes

## First Production Shape

Run `sql/biologic_insurance_change_alerts.sql` every morning and again shortly before biologics hours. The first version should be review-only. It should not hold medication automatically.

The query emits two useful classes of findings:

- `alert_priority = 1`: strong evidence that insurance changed or is missing.
- `alert_priority = 2`: needs human review because IMS data was edited, no dispense was found, or no historical primary row matched the dispense date.

## Where To Alert

Good targets:

- biologics staff queue
- Xolair/Fasenra/Tezspire staff group
- daily report emailed or placed in a shared folder
- IMS todo/reminder only after a dry-run period proves the false-positive rate is acceptable

## Suggested Alert Text

```text
Biologic PA check needed before administration.
Reason: {alert_reason}
Drug: {biologic_drug}
Appointment: {schedule_date} {schedule_time} {office_code}
Last dispense: {dispense_date} {dispense_code}
Current primary: {current_plan_name} / {current_membership_id} / group {current_group_no}
Primary at dispense: {dispense_plan_name} / {dispense_membership_id} / group {dispense_group_no}
```

## Hardening

The strongest long-term version is to snapshot the insurance used for PA approval or dispense. IMS insurance rows can be edited in place, so `patient_insurance.changed_date > dispense_date` is useful but noisy.

Recommended snapshot fields:

```text
patient_id
biologic_drug
source_event
source_tran_id
source_date
insurance_id
insurance_no
membership_id
group_no
group_name
pa_status
pa_expiration_date
resolved_status
resolved_by
resolved_at
```

Then the alert only has to compare the snapshot against the current primary insurance.

## Reminder Creation Shape

The insurance-change-triggered workflow is separate from the appointment audit:

```text
recent dispense within 45 days
+ recent primary insurance row started or was created after the dispense
+ insurance change timestamp is after the dispense date
+ no duplicate open BIO_INS_CHANGE_PA reminder
= create IMS todo reminder
```

If a patient has more than one recent biologic dispense, choose the single most recent dispense across Xolair/Fasenra/Tezspire. Do not create one reminder per drug for the same insurance-change event.

Do not treat `patient_insurance.changed_date` alone as a true insurance change. IMS may update that timestamp for verification or maintenance on an already-active plan, as seen with a Medical Mutual row that started before the biologic dispense.

Reminder mappings:

```text
Xolair   -> category_id 14  -> group_id 14 Biologics
Fasenra  -> category_id 81  -> group_id 14 Biologics
Tezspire -> category_id 123 -> group_id 14 Biologics
```

The insert uses `todo_by_multi_group = ',14,'` and `tobe_doneby = 'Biologics '`, matching IMS's group-assignment pattern for reminders. It also marks rows with `source = 'BIO_INS_CHANGE_PA'` so later runs can avoid duplicates and reports can find automation-created reminders.

The task heading is fixed text: `ALERT! Biologic Dispensed and Insurance changed`. The patient/drug/plan detail belongs in `todo.note`. Leave `tobe_done_detail.note` empty so the My Tasks `Status Note` column stays blank.

IMS task lists do not read `todo` alone. The My Tasks reminder screen joins `todo` to `tobe_done_detail`, so automation-created reminders must insert both rows. The child detail row should use `task_status = 'P'` and a `show_date` on or before the current date; the current script uses seven days before the due date, matching nearby IMS-created reminders.
