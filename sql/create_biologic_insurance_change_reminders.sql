-- Create IMS reminders for recent biologic dispense patients with recent
-- primary insurance changes.
--
-- This file is intended to be run only by scripts/run-biologic-insurance-change-reminders.ps1 -Apply.
-- It inserts into todo and suppresses duplicates using source/ref_tran_id.

INSERT INTO todo (
    tran_id,
    tran_date,
    status,
    forwhom,
    forwhom_name,
    category_id,
    todo,
    todo_at,
    todo_date,
    todo_time,
    tobe_doneby,
    generated_by,
    priority,
    todo_by_multi_group,
    assignto_flag,
    forwhom_id,
    ref_tran_id,
    ref_sr_id,
    note,
    source,
    is_auto,
    created_date,
    changed_date,
    createdby_id,
    changedby_id
)
WITH dispense_charges AS (
    SELECT
        bh.patient_id,
        CASE
            WHEN UPPER(CAST(bm.billing_id AS VARCHAR(30))) IN ('XO150', 'XO300', 'XOL75', 'DISXO')
              OR (
                    UPPER(COALESCE(bm.descr, '')) LIKE '%XOLAIR%'
                AND UPPER(COALESCE(bm.descr, '')) LIKE '%DISP%'
              )
                THEN 'XOLAIR'
            WHEN UPPER(CAST(bm.billing_id AS VARCHAR(30))) IN ('FAS30', 'FASDI', 'DISFA')
              OR (
                    UPPER(COALESCE(bm.descr, '')) LIKE '%FASENRA%'
                AND UPPER(COALESCE(bm.descr, '')) LIKE '%DISP%'
              )
                THEN 'FASENRA'
            WHEN UPPER(CAST(bm.billing_id AS VARCHAR(30))) IN ('TEZ', 'DISTE')
              OR (
                    UPPER(COALESCE(bm.descr, '')) LIKE '%TEZSPIRE%'
                AND UPPER(COALESCE(bm.descr, '')) LIKE '%DISP%'
              )
                THEN 'TEZSPIRE'
            ELSE NULL
        END AS biologic_drug,
        bd.service_date AS dispense_date,
        bd.tran_id AS dispense_tran_id,
        bd.sr_id AS dispense_sr_id,
        CAST(bm.billing_id AS VARCHAR(30)) AS dispense_code
    FROM billing_detail bd
    JOIN billing_header bh
      ON bh.tran_id = bd.tran_id
    JOIN billing_master bm
      ON bm.billing_id = bd.billing_id
    WHERE bd.service_date >= DATEADD(day, -__DISPENSE_LOOKBACK_DAYS__, TODAY())
      AND bh.patient_id IS NOT NULL
      AND (
            UPPER(CAST(bm.billing_id AS VARCHAR(30))) IN (
                'XO150', 'XO300', 'XOL75', 'DISXO',
                'FAS30', 'FASDI', 'DISFA',
                'TEZ', 'DISTE'
            )
         OR (
                UPPER(COALESCE(bm.descr, '')) LIKE '%DISP%'
            AND (
                   UPPER(COALESCE(bm.descr, '')) LIKE '%XOLAIR%'
                OR UPPER(COALESCE(bm.descr, '')) LIKE '%FASENRA%'
                OR UPPER(COALESCE(bm.descr, '')) LIKE '%TEZSPIRE%'
            )
         )
      )
),
last_dispense AS (
    SELECT *
    FROM (
        SELECT
            dc.*,
            ROW_NUMBER() OVER (
                PARTITION BY dc.patient_id
                ORDER BY dc.dispense_date DESC, dc.dispense_tran_id DESC, dc.dispense_sr_id DESC
            ) AS rn
        FROM dispense_charges dc
        WHERE dc.biologic_drug IS NOT NULL
    ) x
    WHERE rn = 1
),
recent_primary_change AS (
    SELECT *
    FROM (
        SELECT
            pi.patient_id,
            pi.sr_id AS insurance_sr_id,
            pi.start_date,
            pi.created_date,
            COALESCE(pi.changed_date, pi.created_date) AS insurance_change_ts,
            ROW_NUMBER() OVER (
                PARTITION BY pi.patient_id
                ORDER BY COALESCE(pi.changed_date, pi.created_date, pi.start_date) DESC, pi.sr_id DESC
            ) AS rn
        FROM patient_insurance pi
        WHERE pi.priority = 'P'
          AND COALESCE(pi.changed_date, pi.created_date) >= DATEADD(day, -__CHANGE_LOOKBACK_DAYS__, CURRENT TIMESTAMP)
    ) x
    WHERE rn = 1
),
current_primary AS (
    SELECT *
    FROM (
        SELECT
            pi.patient_id,
            pi.insurance_id AS current_insurance_id,
            pi.insurance_no AS current_insurance_no,
            pi.membership_id AS current_membership_id,
            pi.group_no AS current_group_no,
            ROW_NUMBER() OVER (
                PARTITION BY pi.patient_id
                ORDER BY COALESCE(pi.changed_date, pi.created_date, pi.start_date) DESC, pi.sr_id DESC
            ) AS rn
        FROM patient_insurance pi
        WHERE COALESCE(pi.active, 'Y') = 'Y'
          AND pi.priority = 'P'
          AND (pi.start_date IS NULL OR pi.start_date <= TODAY())
          AND (pi.end_date IS NULL OR pi.end_date >= TODAY())
    ) x
    WHERE rn = 1
),
candidates AS (
    SELECT
        CASE ld.biologic_drug
            WHEN 'XOLAIR' THEN 14
            WHEN 'FASENRA' THEN 81
            WHEN 'TEZSPIRE' THEN 123
        END AS category_id,
        ld.biologic_drug,
        ld.patient_id,
        COALESCE(pm.lastname, '') + ', ' + COALESCE(pm.firstname, '') AS patient_name,
        ld.dispense_date,
        ld.dispense_tran_id,
        ld.dispense_sr_id,
        ld.dispense_code,
        ipm_current.name AS current_plan_name,
        cp.current_insurance_no,
        cp.current_membership_id,
        cp.current_group_no,
        'Insurance changed after recent ' || ld.biologic_drug ||
            ' dispense - verify PA/no-PA before administration. Last dispense ' ||
            COALESCE(CAST(ld.dispense_date AS VARCHAR(30)), '') || ' ' ||
            COALESCE(ld.dispense_code, '') || '. Current primary: ' ||
            COALESCE(ipm_current.name, '(none)') || ', member ' ||
            COALESCE(cp.current_membership_id, cp.current_insurance_no, '') ||
            ', group ' || COALESCE(cp.current_group_no, '') || '.' AS reminder_text
    FROM last_dispense ld
    JOIN recent_primary_change rpc
      ON rpc.patient_id = ld.patient_id
     AND rpc.insurance_change_ts >= CAST(ld.dispense_date AS TIMESTAMP)
     AND (
            (rpc.start_date IS NOT NULL AND rpc.start_date >= ld.dispense_date)
         OR rpc.created_date >= CAST(ld.dispense_date AS TIMESTAMP)
     )
    LEFT JOIN patient_master pm
      ON pm.id = ld.patient_id
    LEFT JOIN current_primary cp
      ON cp.patient_id = ld.patient_id
    LEFT JOIN insurance_plan_master ipm_current
      ON ipm_current.srno = cp.current_insurance_id
    WHERE NOT EXISTS (
        SELECT 1
        FROM todo t
        WHERE t.status = 'G'
          AND t.forwhom = 'P'
          AND t.forwhom_id = ld.patient_id
          AND COALESCE(t.source, '') = 'BIO_INS_CHANGE_PA'
    )
    __APPLY_EXTRA_WHERE__
)
SELECT
    seq_todo_id.NEXTVAL,
    TODAY(),
    'G',
    'P',
    patient_name,
    category_id,
    'ALERT! Biologic Dispensed and Insurance changed',
    'T',
    TODAY(),
    CURRENT TIME,
    'Biologics ',
    'U',
    'H',
    ',14,',
    'N',
    patient_id,
    dispense_tran_id,
    dispense_sr_id,
    reminder_text,
    'BIO_INS_CHANGE_PA',
    'Y',
    CURRENT TIMESTAMP,
    CURRENT TIMESTAMP,
    -1,
    -1
FROM candidates;

INSERT INTO tobe_done_detail (
    tran_id,
    todo_id,
    todo_date,
    todo_at,
    iteration,
    task_status,
    show_date,
    created_date,
    changed_date,
    createdby_id,
    changedby_id
)
SELECT
    base.max_tran_id + ROW_NUMBER() OVER (ORDER BY t.tran_id),
    t.tran_id,
    t.todo_date,
    t.todo_at,
    t.iteration,
    'P',
    DATEADD(day, -7, t.todo_date),
    CURRENT TIMESTAMP,
    CURRENT TIMESTAMP,
    t.createdby_id,
    t.changedby_id
FROM todo t
CROSS JOIN (
    SELECT COALESCE(MAX(tran_id), 0) AS max_tran_id
    FROM tobe_done_detail
) base
WHERE t.status = 'G'
  AND t.source = 'BIO_INS_CHANGE_PA'
  AND NOT EXISTS (
      SELECT 1
      FROM tobe_done_detail d
      WHERE d.todo_id = t.tran_id
  );

SELECT 'OPEN_BIO_INS_CHANGE_PA_REMINDERS|' || CAST(COUNT(*) AS VARCHAR(20))
FROM todo
WHERE status = 'G'
  AND source = 'BIO_INS_CHANGE_PA';
