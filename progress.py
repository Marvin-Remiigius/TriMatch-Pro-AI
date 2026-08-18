"""
Baseline-vs-latest lab progress tracking for enrolled patients.

Two distinct comparisons, kept separate throughout:
- baseline = a patient's lab result nearest patient_trial.baseline_date
  (on or before it) for a given test_code.
- latest = that patient's most recent lab result for the same test_code,
  regardless of baseline_date.

Direction (whether a deviation is an improvement) is test-specific and only
ever looked up, never guessed -- a test_code not in TEST_DIRECTION is
reported "indeterminate", matching the matcher's "never guess" principle.
"""

TEST_DIRECTION = {
    "HBA1C": "lower_is_better",
    "GLU": "lower_is_better",
    "CHOL": "lower_is_better",
    "CREAT": "lower_is_better",
    "ALT": "lower_is_better",
    "AST": "lower_is_better",
    "EGFR": "higher_is_better",
    "HGB": "higher_is_better",
    # WBC deliberately has no direction -- too context-dependent to call
    # "better" or "worse" from the number alone.
}


def _status_for_deviation(test_code: str, deviation: float) -> str:
    direction = TEST_DIRECTION.get(test_code)
    if direction is None:
        return "indeterminate"
    if deviation == 0:
        return "indeterminate"
    if direction == "higher_is_better":
        return "improved" if deviation > 0 else "worsened"
    return "improved" if deviation < 0 else "worsened"


def get_patient_test_progress(client, patient_id: str, baseline_date: str | None) -> list[dict]:
    """Returns one entry per test_code the patient has ANY lab_results for:
    baseline value/date/source, latest value/date/source, deviation, and
    status (improved/worsened/indeterminate/no_data)."""
    all_labs = (
        client.table("lab_results")
        .select("lab_result_id, test_code, test_name, value, unit, test_date")
        .eq("patient_id", patient_id)
        .order("test_date", desc=False)
        .execute()
        .data
    )

    by_test = {}
    for row in all_labs:
        by_test.setdefault(row["test_code"], []).append(row)

    results = []
    for test_code, rows in by_test.items():
        rows_sorted = sorted(rows, key=lambda r: r["test_date"])
        latest = rows_sorted[-1]

        # baseline_date is None for a patient who withdrew before ever
        # being enrolled (withdraw_patient allows withdrawing straight
        # from invited/accepted/consented; only enroll_patient sets
        # baseline_date) -- there's no anchor to compare against, so
        # treat it the same as "no reading on/before baseline" below
        # rather than guessing or crashing.
        baseline_candidates = (
            [r for r in rows_sorted if r["test_date"] <= baseline_date]
            if baseline_date is not None
            else []
        )
        baseline = baseline_candidates[-1] if baseline_candidates else None

        if baseline is None:
            results.append(
                {
                    "test_code": test_code,
                    "test_name": latest["test_name"],
                    "unit": latest["unit"],
                    "baseline_value": None,
                    "baseline_date": None,
                    "baseline_lab_result_id": None,
                    "latest_value": latest["value"],
                    "latest_date": latest["test_date"],
                    "latest_lab_result_id": latest["lab_result_id"],
                    "deviation": None,
                    "status": "no_data",
                }
            )
            continue

        if baseline["lab_result_id"] == latest["lab_result_id"]:
            # Only one reading exists (on/before baseline_date) -- no actual
            # follow-up has been recorded yet. Showing "0 deviation" here
            # would misleadingly imply a comparison was made.
            results.append(
                {
                    "test_code": test_code,
                    "test_name": baseline["test_name"],
                    "unit": baseline["unit"],
                    "baseline_value": baseline["value"],
                    "baseline_date": baseline["test_date"],
                    "baseline_lab_result_id": baseline["lab_result_id"],
                    "latest_value": None,
                    "latest_date": None,
                    "latest_lab_result_id": None,
                    "deviation": None,
                    "status": "no_data",
                }
            )
            continue

        deviation = round(latest["value"] - baseline["value"], 4)
        results.append(
            {
                "test_code": test_code,
                "test_name": latest["test_name"],
                "unit": latest["unit"],
                "baseline_value": baseline["value"],
                "baseline_date": baseline["test_date"],
                "baseline_lab_result_id": baseline["lab_result_id"],
                "latest_value": latest["value"],
                "latest_date": latest["test_date"],
                "latest_lab_result_id": latest["lab_result_id"],
                "deviation": deviation,
                "status": _status_for_deviation(test_code, deviation),
            }
        )

    results.sort(key=lambda r: r["test_code"])
    return results


def _pick_primary_test_code(patient_progress: list[dict], requested: str | None) -> str | None:
    """Picks which test_code success_rate is computed against. Never
    guessed from the trial's primary_endpoint text (e.g. "Time to
    Recovery") -- that's free text with no reliable mapping to a lab test.
    If not explicitly requested, falls back to whichever direction-defined
    test_code has the most enrolled patients with a determinable
    (improved/worsened) status, so the metric is grounded in real data."""
    if requested:
        return requested

    coverage = {}
    for tests in patient_progress:
        for t in tests:
            if t["status"] in ("improved", "worsened") and TEST_DIRECTION.get(t["test_code"]):
                coverage[t["test_code"]] = coverage.get(t["test_code"], 0) + 1

    if not coverage:
        return None
    return max(coverage, key=coverage.get)


def compute_trial_progress(client, nct_id: str, primary_test_code: str | None = None) -> dict:
    """Computes per-patient baseline-vs-latest progress for every patient
    enrolled (status in enrolled/withdrawn) in a trial, plus a trial-level
    summary. Does NOT write to trial_metrics -- callers that want that
    persisted call compute_and_store_trial_metrics instead."""
    enrollments = (
        client.table("patient_trial")
        .select("patient_id, status, baseline_date")
        .eq("nct_id", nct_id)
        .in_("status", ["enrolled", "withdrawn"])
        .execute()
        .data
    )

    patients = []
    all_tests = []
    for e in enrollments:
        tests = get_patient_test_progress(client, e["patient_id"], e["baseline_date"])
        all_tests.append(tests)
        patients.append(
            {
                "patient_id": e["patient_id"],
                "status": e["status"],
                "baseline_date": e["baseline_date"],
                "tests": tests,
            }
        )

    enrolled_count = len(enrollments)
    active_count = sum(1 for e in enrollments if e["status"] == "enrolled")
    dropouts = sum(1 for e in enrollments if e["status"] == "withdrawn")

    chosen_test_code = _pick_primary_test_code(all_tests, primary_test_code)

    # success_rate definition: the fraction of ALL enrolled patients (both
    # currently active and withdrawn -- withdrawn patients still count in
    # the denominator, they just can't be hidden from it) whose status on
    # chosen_test_code is "improved". Patients with no_data/indeterminate
    # on that test count against the rate, not for it -- this is
    # deliberately conservative rather than excluding them from the
    # denominator, which would inflate the number. This measures the
    # cohort's trajectory vs. its own baseline, NOT a controlled trial
    # result -- there is no control arm here.
    improved_count = 0
    if chosen_test_code:
        for tests in all_tests:
            for t in tests:
                if t["test_code"] == chosen_test_code and t["status"] == "improved":
                    improved_count += 1
                    break

    success_rate = (improved_count / enrolled_count) if enrolled_count else None

    return {
        "nct_id": nct_id,
        "enrolled": enrolled_count,
        "active": active_count,
        "dropouts": dropouts,
        "success_rate": success_rate,
        "primary_test_code_used": chosen_test_code,
        "improved_count": improved_count,
        "patients": patients,
    }


def store_trial_metrics(client, progress: dict) -> None:
    client.table("trial_metrics").upsert(
        {
            "nct_id": progress["nct_id"],
            "enrolled": progress["enrolled"],
            "active": progress["active"],
            "dropouts": progress["dropouts"],
            "success_rate": progress["success_rate"],
        },
        on_conflict="nct_id",
    ).execute()
