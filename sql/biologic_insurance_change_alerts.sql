-- IMS Biologic insurance-change audit
-- SQL Anywhere / Meditab IMS
--
-- Purpose:
--   For upcoming Xolair, Fasenra, and Tezspire appointments, compare the
--   patient's current primary insurance against the primary insurance active on
--   the patient's most recent matching dispense charge.
--
-- Notes:
--   schedule_detail.procedure_id:
--     21 = Xolair
--     53 = Fasenra
--     55 = Tezspire
--
--   Dispense billing IDs observed in IMS:
--     Xolair:   XO150, XO300, XOL75, DISXO
--     Fasenra:  FAS30, FASDI, DISFA
--     Tezspire: TEZ, DISTE

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
    WHERE bd.service_date >= DATEADD(day, -365, TODAY())
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
                PARTITION BY dc.patient_id, dc.biologic_drug
                ORDER BY dc.dispense_date DESC, dc.dispense_tran_id DESC, dc.dispense_sr_id DESC
            ) AS rn
        FROM dispense_charges dc
        WHERE dc.biologic_drug IS NOT NULL
    ) x
    WHERE rn = 1
),
current_primary AS (
    SELECT *
    FROM (
        SELECT
            pi.patient_id,
            pi.sr_id,
            pi.insurance_id,
            pi.insurance_no,
            pi.membership_id,
            pi.group_no,
            pi.group_name,
            pi.start_date,
            pi.end_date,
            pi.changed_date,
            pi.created_date,
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
primary_at_dispense AS (
    SELECT *
    FROM (
        SELECT
            ld.patient_id,
            ld.biologic_drug,
            pi.sr_id,
            pi.insurance_id,
            pi.insurance_no,
            pi.membership_id,
            pi.group_no,
            pi.group_name,
            pi.start_date,
            pi.end_date,
            pi.changed_date,
            pi.created_date,
            ROW_NUMBER() OVER (
                PARTITION BY ld.patient_id, ld.biologic_drug
                ORDER BY COALESCE(pi.start_date, CAST('1900-01-01' AS DATE)) DESC, pi.sr_id DESC
            ) AS rn
        FROM last_dispense ld
        JOIN patient_insurance pi
          ON pi.patient_id = ld.patient_id
         AND pi.priority = 'P'
         AND (pi.start_date IS NULL OR pi.start_date <= ld.dispense_date)
         AND (pi.end_date IS NULL OR pi.end_date >= ld.dispense_date)
    ) x
    WHERE rn = 1
),
next_biologic_schedule AS (
    SELECT *
    FROM (
        SELECT
            sd.tran_id AS schedule_tran_id,
            sd.forwhom_id AS patient_id,
            sd.forwhom_name AS schedule_patient_name,
            sd.procedure_id,
            CASE sd.procedure_id
                WHEN 21 THEN 'XOLAIR'
                WHEN 53 THEN 'FASENRA'
                WHEN 55 THEN 'TEZSPIRE'
                ELSE NULL
            END AS biologic_drug,
            sd.schedule_date,
            sd.schedule_time,
            sd.office_id,
            sd.status AS schedule_status,
            sd.schedule_note,
            ROW_NUMBER() OVER (
                PARTITION BY sd.forwhom_id, sd.procedure_id
                ORDER BY sd.schedule_date, sd.schedule_time
            ) AS rn
        FROM schedule_detail sd
        WHERE sd.schedule_date BETWEEN TODAY() AND DATEADD(day, 14, TODAY())
          AND sd.procedure_id IN (21, 53, 55)
          AND sd.forwhom_id IS NOT NULL
          AND sd.canceled_date IS NULL
    ) x
    WHERE rn = 1
),
alert_base AS (
    SELECT
        n.schedule_tran_id,
        n.patient_id,
        pm.patient_no,
        COALESCE(pm.lastname, '') + ', ' + COALESCE(pm.firstname, '') AS patient_name,
        n.biologic_drug,
        n.procedure_id,
        n.schedule_date,
        n.schedule_time,
        n.office_id,
        om.office_code,
        n.schedule_status,
        n.schedule_note,
        ld.dispense_date,
        ld.dispense_tran_id,
        ld.dispense_code,
        ld.dispense_descr,
        cp.insurance_id AS current_insurance_id,
        cipm.name AS current_plan_name,
        cicm.name AS current_carrier_name,
        cp.insurance_no AS current_insurance_no,
        cp.membership_id AS current_membership_id,
        cp.group_no AS current_group_no,
        cp.group_name AS current_group_name,
        cp.start_date AS current_start_date,
        cp.end_date AS current_end_date,
        cp.changed_date AS current_changed_date,
        pad.insurance_id AS dispense_insurance_id,
        dipm.name AS dispense_plan_name,
        dicm.name AS dispense_carrier_name,
        pad.insurance_no AS dispense_insurance_no,
        pad.membership_id AS dispense_membership_id,
        pad.group_no AS dispense_group_no,
        pad.group_name AS dispense_group_name,
        pad.start_date AS dispense_ins_start_date,
        pad.end_date AS dispense_ins_end_date,
        pad.changed_date AS dispense_ins_changed_date,
        CASE
            WHEN ld.patient_id IS NULL THEN 'NO RECENT DISPENSE FOUND'
            WHEN cp.patient_id IS NULL THEN 'NO CURRENT PRIMARY INSURANCE'
            WHEN pad.patient_id IS NULL THEN 'NO PRIMARY INSURANCE FOUND AT LAST DISPENSE'
            WHEN COALESCE(cp.insurance_id, -1) <> COALESCE(pad.insurance_id, -1) THEN 'PRIMARY PLAN CHANGED'
            WHEN COALESCE(cp.insurance_no, '') <> COALESCE(pad.insurance_no, '') THEN 'SUBSCRIBER/INSURANCE NO CHANGED'
            WHEN COALESCE(cp.membership_id, '') <> COALESCE(pad.membership_id, '') THEN 'MEMBER ID CHANGED'
            WHEN COALESCE(cp.group_no, '') <> COALESCE(pad.group_no, '') THEN 'GROUP NO CHANGED'
            WHEN cp.start_date IS NOT NULL AND cp.start_date > ld.dispense_date THEN 'CURRENT PRIMARY STARTS AFTER DISPENSE'
            WHEN cp.changed_date IS NOT NULL AND cp.changed_date > ld.dispense_date THEN 'CURRENT PRIMARY EDITED AFTER DISPENSE'
            ELSE 'NO ALERT'
        END AS alert_reason,
        CASE
            WHEN ld.patient_id IS NULL THEN 2
            WHEN cp.patient_id IS NULL THEN 1
            WHEN pad.patient_id IS NULL THEN 2
            WHEN COALESCE(cp.insurance_id, -1) <> COALESCE(pad.insurance_id, -1) THEN 1
            WHEN COALESCE(cp.insurance_no, '') <> COALESCE(pad.insurance_no, '') THEN 1
            WHEN COALESCE(cp.membership_id, '') <> COALESCE(pad.membership_id, '') THEN 1
            WHEN COALESCE(cp.group_no, '') <> COALESCE(pad.group_no, '') THEN 1
            WHEN cp.start_date IS NOT NULL AND cp.start_date > ld.dispense_date THEN 1
            WHEN cp.changed_date IS NOT NULL AND cp.changed_date > ld.dispense_date THEN 2
            ELSE 9
        END AS alert_priority
    FROM next_biologic_schedule n
    LEFT JOIN patient_master pm
      ON pm.id = n.patient_id
    LEFT JOIN office_master om
      ON om.srno = n.office_id
    LEFT JOIN last_dispense ld
      ON ld.patient_id = n.patient_id
     AND ld.biologic_drug = n.biologic_drug
    LEFT JOIN current_primary cp
      ON cp.patient_id = n.patient_id
    LEFT JOIN primary_at_dispense pad
      ON pad.patient_id = n.patient_id
     AND pad.biologic_drug = n.biologic_drug
    LEFT JOIN insurance_plan_master cipm
      ON cipm.srno = cp.insurance_id
    LEFT JOIN insurance_carrier_master cicm
      ON cicm.srno = cipm.carrier_id
    LEFT JOIN insurance_plan_master dipm
      ON dipm.srno = pad.insurance_id
    LEFT JOIN insurance_carrier_master dicm
      ON dicm.srno = dipm.carrier_id
)
SELECT
    alert_priority,
    alert_reason,
    biologic_drug,
    schedule_date,
    schedule_time,
    office_code,
    patient_id,
    patient_no,
    patient_name,
    schedule_status,
    dispense_date,
    dispense_code,
    dispense_descr,
    current_insurance_id,
    current_plan_name,
    current_carrier_name,
    current_insurance_no,
    current_membership_id,
    current_group_no,
    current_start_date,
    current_changed_date,
    dispense_insurance_id,
    dispense_plan_name,
    dispense_carrier_name,
    dispense_insurance_no,
    dispense_membership_id,
    dispense_group_no,
    dispense_ins_start_date,
    dispense_ins_end_date,
    schedule_tran_id,
    dispense_tran_id
FROM alert_base
WHERE alert_reason <> 'NO ALERT'
ORDER BY alert_priority, biologic_drug, schedule_date, schedule_time, patient_name;

