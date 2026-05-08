-- IMS Biologic insurance-change reminder candidates
-- SQL Anywhere / Meditab IMS
--
-- Purpose:
--   Find patients with a Xolair, Fasenra, or Tezspire dispense in the last
--   __DISPENSE_LOOKBACK_DAYS__ days whose primary insurance row was created or
--   changed in the last __CHANGE_LOOKBACK_DAYS__ days after that dispense.
--
-- Reminder routing:
--   Xolair   -> todo_category.category_id 14  (XOLAIR)
--   Fasenra  -> todo_category.category_id 81  (FASENRA)
--   Tezspire -> todo_category.category_id 123 (Tezspire)
--   Forward  -> todo_group.group_id 14        (Biologics)

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
        CAST(bm.billing_id AS VARCHAR(30)) AS dispense_code,
        bm.descr AS dispense_descr
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
            pi.insurance_id,
            pi.insurance_no,
            pi.membership_id,
            pi.group_no,
            pi.group_name,
            pi.priority,
            pi.active,
            pi.start_date,
            pi.end_date,
            pi.created_date,
            pi.changed_date,
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
            pi.sr_id AS current_insurance_sr_id,
            pi.insurance_id AS current_insurance_id,
            pi.insurance_no AS current_insurance_no,
            pi.membership_id AS current_membership_id,
            pi.group_no AS current_group_no,
            pi.group_name AS current_group_name,
            pi.start_date AS current_start_date,
            pi.end_date AS current_end_date,
            pi.created_date AS current_created_date,
            pi.changed_date AS current_changed_date,
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
        14 AS biologics_group_id,
        ',14,' AS todo_by_multi_group,
        'Biologics ' AS tobe_doneby,
        ld.biologic_drug,
        ld.patient_id,
        pm.patient_no,
        COALESCE(pm.lastname, '') + ', ' + COALESCE(pm.firstname, '') AS patient_name,
        ld.dispense_date,
        ld.dispense_tran_id,
        ld.dispense_sr_id,
        ld.dispense_code,
        ld.dispense_descr,
        rpc.insurance_change_ts,
        rpc.insurance_sr_id AS changed_insurance_sr_id,
        rpc.insurance_id AS changed_insurance_id,
        ipm_changed.name AS changed_plan_name,
        icm_changed.name AS changed_carrier_name,
        rpc.insurance_no AS changed_insurance_no,
        rpc.membership_id AS changed_membership_id,
        rpc.group_no AS changed_group_no,
        rpc.group_name AS changed_group_name,
        rpc.active AS changed_insurance_active,
        rpc.start_date AS changed_insurance_start_date,
        rpc.end_date AS changed_insurance_end_date,
        cp.current_insurance_id,
        ipm_current.name AS current_plan_name,
        icm_current.name AS current_carrier_name,
        cp.current_insurance_no,
        cp.current_membership_id,
        cp.current_group_no,
        cp.current_group_name,
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
    LEFT JOIN insurance_plan_master ipm_changed
      ON ipm_changed.srno = rpc.insurance_id
    LEFT JOIN insurance_carrier_master icm_changed
      ON icm_changed.srno = ipm_changed.carrier_id
    LEFT JOIN insurance_plan_master ipm_current
      ON ipm_current.srno = cp.current_insurance_id
    LEFT JOIN insurance_carrier_master icm_current
      ON icm_current.srno = ipm_current.carrier_id
    WHERE NOT EXISTS (
        SELECT 1
        FROM todo t
        WHERE t.status = 'G'
          AND t.forwhom = 'P'
          AND t.forwhom_id = ld.patient_id
          AND COALESCE(t.source, '') = 'BIO_INS_CHANGE_PA'
    )
)
SELECT
    category_id,
    biologics_group_id,
    biologic_drug,
    patient_id,
    patient_no,
    patient_name,
    dispense_date,
    dispense_code,
    dispense_descr,
    insurance_change_ts,
    changed_insurance_id,
    changed_plan_name,
    changed_carrier_name,
    changed_membership_id,
    changed_group_no,
    changed_insurance_active,
    changed_insurance_start_date,
    changed_insurance_end_date,
    current_insurance_id,
    current_plan_name,
    current_carrier_name,
    current_membership_id,
    current_group_no,
    reminder_text,
    dispense_tran_id,
    dispense_sr_id,
    changed_insurance_sr_id
FROM candidates
ORDER BY biologic_drug, insurance_change_ts DESC, patient_name;
