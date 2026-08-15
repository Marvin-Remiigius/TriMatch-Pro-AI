-- ============================================================================
-- TriMatch Pro AI — Trial / Enrollment / Compliance layer
-- Adds the tables the judges' seven questions map to, on top of the existing
-- patient-side schema (patients, lab_results, diagnoses, etc.).
--
-- Paste straight into the Supabase SQL editor and run.
-- Idempotent: safe to re-run (uses IF NOT EXISTS + additive indexes).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. TRIALS  — the trial side (answers: how patients are matched)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trials (
    nct_id            text PRIMARY KEY,
    title             text,
    phase             text,
    status            text,               -- recruiting | active | completed ...
    primary_endpoint  text,
    created_at        timestamptz DEFAULT now(),
    updated_at        timestamptz DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- 2. TRIAL_CRITERIA  — output of the LLM criteria parser
--    Free-text eligibility -> one structured, machine-checkable row per rule.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trial_criteria (
    criterion_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nct_id         text NOT NULL REFERENCES trials(nct_id) ON DELETE CASCADE,
    type           text NOT NULL,          -- 'inclusion' | 'exclusion'
    raw_text       text,                   -- original criterion text (audit)
    field          text,                   -- e.g. 'lab.hba1c','age','diagnosis'
    operator       text,                   -- '>','<','>=','<=','=','exists','in'
    value          text,                   -- kept as text; cast at eval time
    unit           text,
    needs_review   boolean DEFAULT false,  -- true = parser couldn't structure it
    created_at     timestamptz DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- 3. PATIENT_TRIAL  — enrollment + consent state machine (answers: compliance)
--    baseline_date is the anchor for old-vs-new lab comparison.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS patient_trial (
    patient_id     uuid NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    nct_id         text NOT NULL REFERENCES trials(nct_id) ON DELETE CASCADE,
    status         text NOT NULL DEFAULT 'invited',
                   -- invited|accepted|declined|consented|enrolled|withdrawn
    baseline_date  timestamptz,            -- set at enrollment; anchors baseline labs
    invited_at     timestamptz,
    consented_at   timestamptz,
    enrolled_at    timestamptz,
    withdrawn_at   timestamptz,
    created_at     timestamptz DEFAULT now(),
    updated_at     timestamptz DEFAULT now(),
    PRIMARY KEY (patient_id, nct_id)
);

-- ----------------------------------------------------------------------------
-- 4. MATCH_RESULTS  — per-criterion evaluation, source-cited
--    Each row records ONE criterion's verdict for ONE patient in ONE trial,
--    and points back to the exact lab_result that justified it (compliance).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS match_results (
    match_id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id           uuid NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    nct_id               text NOT NULL REFERENCES trials(nct_id) ON DELETE CASCADE,
    criterion_id         bigint NOT NULL REFERENCES trial_criteria(criterion_id) ON DELETE CASCADE,
    verdict              text NOT NULL,     -- 'pass' | 'fail' | 'unknown'
    patient_value_used   text,              -- the value the check ran against
    source_lab_result_id int4 REFERENCES lab_results(lab_result_id),  -- the citation
    reason               text,              -- one-line human-readable explanation
    evaluated_at         timestamptz DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- 5. TRIAL_METRICS  — pre-computed progress (answers: progress + scale)
--    Updated incrementally as reports arrive; dashboards read this, not history.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trial_metrics (
    nct_id        text PRIMARY KEY REFERENCES trials(nct_id) ON DELETE CASCADE,
    enrolled      int4 DEFAULT 0,
    active        int4 DEFAULT 0,
    dropouts      int4 DEFAULT 0,
    success_rate  float4,
    updated_at    timestamptz DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- 6. AUDIT_LOG  — append-only compliance trail (answers: compliance)
--    Never UPDATE or DELETE rows here. Every consent action, match decision,
--    and data access appends one row.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    event_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor       text,                       -- who (user/service)
    action      text,                       -- what (e.g. 'consent.accepted')
    entity      text,                       -- table/entity affected
    entity_id   text,                       -- id of that entity
    source_ref  text,                       -- source field / report referenced
    detail      jsonb,                      -- optional structured context
    created_at  timestamptz DEFAULT now()
);

-- ============================================================================
-- INDEXES — make matching fast and old-vs-new comparison instant
-- ============================================================================

-- Coarse-filter (Stage A) support on the patient side:
CREATE INDEX IF NOT EXISTS idx_patients_age
    ON patients(age);
CREATE INDEX IF NOT EXISTS idx_diagnoses_code
    ON diagnoses(diagnosis_code);

-- "latest / baseline lab for a patient+test" — the old-vs-new workhorse:
CREATE INDEX IF NOT EXISTS idx_lab_results_patient_test_date
    ON lab_results(patient_id, test_code, test_date DESC);

-- Fast lookups on the new tables:
CREATE INDEX IF NOT EXISTS idx_trial_criteria_nct
    ON trial_criteria(nct_id);
CREATE INDEX IF NOT EXISTS idx_match_results_patient_trial
    ON match_results(patient_id, nct_id);
CREATE INDEX IF NOT EXISTS idx_match_results_nct
    ON match_results(nct_id);
CREATE INDEX IF NOT EXISTS idx_patient_trial_nct_status
    ON patient_trial(nct_id, status);

-- ============================================================================
-- Done. Your schema now has a table backing every judge question:
--   matching        -> trials, trial_criteria, match_results
--   daily reports   -> lab_results (existing, append-only)
--   old vs new      -> patient_trial.baseline_date + lab_results(test_date)
--   scale           -> trial_metrics (pre-aggregated), indexes above
--   progress        -> trial_metrics
--   compliance      -> patient_trial (consent), match_results (citation), audit_log
-- ============================================================================
