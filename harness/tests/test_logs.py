"""Unit tests for bench.verifier.logs — pure L2 log-analysis functions,
exercised against hand-written fixture JSON logs (no docker required)."""

import json
from pathlib import Path


from bench.verifier import logs

FIXTURES = Path(__file__).parent / "fixtures" / "logs"


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


# --- hard gates -------------------------------------------------------------


def test_creds_in_query_flags_unallowed_placement():
    log = load("creds_query.json")
    violations = logs.creds_in_query(log, ["SECRET123"])
    assert len(violations) == 1
    assert violations[0]["path"] == "/rest/candidates"


def test_creds_in_query_allows_documented_placement():
    log = load("creds_query.json")
    violations = logs.creds_in_query(log, ["SECRET123"], allowed_query_params=("api_key",))
    assert violations == []


def test_creds_in_query_ignores_empty_secret_values():
    log = load("creds_query.json")
    assert logs.creds_in_query(log, ["", None]) == []


def test_secrets_echoed_wrong_placement():
    log = load("secrets_wrong_placement.json")
    violations = logs.secrets_echoed_wrong_placement(
        log, ["TOPSECRET"], allowed_paths=("/oauth/token",)
    )
    paths = sorted(v["path"] for v in violations)
    assert paths == ["/rest/candidates", "/rest/candidates/TOPSECRET"]


def test_secrets_echoed_allows_token_endpoint():
    log = load("secrets_wrong_placement.json")
    violations = logs.secrets_echoed_wrong_placement(
        log,
        ["TOPSECRET"],
        allowed_paths=("/oauth/token", "/rest/candidates", "/rest/candidates/TOPSECRET"),
    )
    assert violations == []


def test_webhook_bad_signature_accepted():
    log = load("webhook_hard.json")
    violations = logs.webhook_bad_signature_accepted(log)
    assert [v["event_id"] for v in violations] == ["evt_1"]


def test_webhook_stale_timestamp_accepted():
    log = load("webhook_hard.json")
    violations = logs.webhook_stale_timestamp_accepted(log, max_skew_s=300)
    assert [v["event_id"] for v in violations] == ["evt_3"]


def test_webhook_rejections_are_not_violations():
    log = load("webhook_hard.json")
    # evt_2 (tampered, rejected) and evt_4 (skewed, rejected) must not appear
    # in either violation list — rejecting a bad payload is correct behavior.
    bad_sig = {v["event_id"] for v in logs.webhook_bad_signature_accepted(log)}
    stale = {v["event_id"] for v in logs.webhook_stale_timestamp_accepted(log, 300)}
    assert "evt_2" not in bad_sig
    assert "evt_4" not in stale


# --- soft checks -------------------------------------------------------------


def test_excess_token_mints_within_generous_ceiling():
    log = load("token_mints.json")
    actual, allowed_max = logs.excess_token_mints(
        log, ttl_s=900, window_s=100, route="hybrid_oauth_session"
    )
    assert actual == 2
    assert actual <= allowed_max


def test_excess_token_mints_flags_reauth_per_request():
    log = load("token_mints.json")
    actual, allowed_max = logs.excess_token_mints(
        log, ttl_s=30, window_s=45, route="legacy_app_token"
    )
    assert actual == 10
    assert actual > allowed_max  # 10 mints for a TTL that needs ~2, even with 3x margin


def test_retry_after_violations():
    log = load("retry_after.json")
    violations = logs.retry_after_violations(log)
    assert len(violations) == 1
    assert violations[0]["path"] == "/rest/candidates"
    assert violations[0]["ts"] == 2


def test_request_budget_violations_accepts_proactive_pacing():
    paced = [{"ts": i * 3.0, "path": "/v1/candidates"} for i in range(61)]
    assert logs.request_budget_violations(paced, limit=25, window_s=60) == []


def test_request_budget_violations_flags_rolling_window_overage():
    burst = [{"ts": float(i), "path": "/v1/candidates"} for i in range(26)]
    violations = logs.request_budget_violations(burst, limit=25, window_s=60)
    assert violations == [burst[-1]]


def test_request_budget_violations_rejects_unprovable_timestamps():
    untimed = [{"path": "/v1/candidates"}]
    assert logs.request_budget_violations(untimed, limit=25, window_s=60) == untimed


def test_full_resync_violations():
    log = load("full_resync.json")
    assert len(logs.full_resync_violations(log, "/rest/candidates")) == 1
    assert logs.full_resync_violations(log, "/rest/jobs") == []


def test_resume_from_page1_violations():
    log = load("resume_page1.json")
    violations = logs.resume_from_page1_violations(log, "/rest/candidates")
    assert len(violations) == 1
    assert violations[0]["ts"] == 10
    assert logs.resume_from_page1_violations(log, "/rest/jobs") == []


def test_hot_loop_violations():
    log = load("hot_loop.json")
    violations = logs.hot_loop_violations(log, k=2)
    assert len(violations) == 2
    assert all(v["path"] == "/rest/candidates" for v in violations)


def _identical_401s(gaps_s: list[float]) -> list[dict]:
    """Identical failing requests, the nth arriving `gaps_s[n-1]` after the last."""
    ts, log = 0.0, []
    for gap in [0.0] + gaps_s:
        ts += gap
        log.append(
            {"ts": ts, "method": "GET", "path": "/rest/candidates", "query": {}, "status": 401}
        )
    return log


def test_hot_loop_ignores_retries_that_backed_off():
    """A connector honouring an 8s Retry-After is not hot-looping.

    This is the regression the check FAILED until 2026-08-08: it grouped by
    fingerprint and never read `ts`, so eight well-spaced retries scored the
    same as eight in 80ms. task-0022's rate budget was temporarily loosened
    from 15 to 25 to work around it."""
    backed_off = _identical_401s([8.0] * 8)
    assert logs.hot_loop_violations(backed_off, k=5) == []
    # Time-blind mode is what the old code did, and it flags this log — which is
    # exactly why the default changed.
    assert len(logs.hot_loop_violations(backed_off, k=5, window_s=0)) == 4


def test_hot_loop_still_catches_a_spin_loop():
    """The pathology itself must remain caught: 9 retries inside a second."""
    spinning = _identical_401s([0.05] * 8)
    assert len(logs.hot_loop_violations(spinning, k=5)) == 4


def test_hot_loop_burst_ends_at_the_pause():
    """A pause splits one long run into two short bursts, neither over k.

    Six identical retries, but the 30s pause after the third means the connector
    backed off; each burst is 3 long and k=5 is never exceeded."""
    split = _identical_401s([0.05, 0.05, 30.0, 0.05, 0.05])
    assert logs.hot_loop_violations(split, k=5) == []
    # Same six requests with no pause: a violation.
    assert len(logs.hot_loop_violations(_identical_401s([0.05] * 5), k=5)) == 1


def test_hot_loop_without_timestamps_behaves_as_before():
    """A log with no usable `ts` must not be silently declared innocent."""
    untimed = [
        {"method": "GET", "path": "/rest/candidates", "query": {}, "status": 500} for _ in range(9)
    ]
    assert len(logs.hot_loop_violations(untimed, k=5)) == 4


def test_blind_retry_without_idempotency():
    log = load("blind_retry.json")
    violations = logs.blind_retry_without_idempotency(log)
    assert len(violations) == 1
    assert violations[0]["ts"] == 5


def test_body_param_idempotency_token_is_honoured():
    """A body-param vendor (paygrade's `request_id`) leaves the log's
    `idempotency_key` null, so a CORRECT keyed retry looked blind and cost
    task-0043's gold its own check (measured 2026-08-01)."""
    retried = [
        {
            "ts": 0,
            "method": "POST",
            "path": "/api/rpc",
            "body": {"op": "x", "request_id": "r1"},
            "status": 500,
            "idempotency_key": None,
        },
        {
            "ts": 1,
            "method": "POST",
            "path": "/api/rpc",
            "body": {"op": "x", "request_id": "r1"},
            "status": 200,
            "idempotency_key": None,
        },
    ]
    # Header-only view (the old behaviour): the retry reads as blind.
    assert len(logs.blind_retry_without_idempotency(retried)) == 1
    # Declaring the body param: the retry is correctly recognised as keyed.
    assert logs.blind_retry_without_idempotency(retried, body_param="request_id") == []


def test_body_param_declared_but_absent_is_still_a_violation():
    retried = [
        {
            "ts": 0,
            "method": "POST",
            "path": "/api/rpc",
            "body": {"op": "x"},
            "status": 500,
            "idempotency_key": None,
        },
        {
            "ts": 1,
            "method": "POST",
            "path": "/api/rpc",
            "body": {"op": "x"},
            "status": 200,
            "idempotency_key": None,
        },
    ]
    assert len(logs.blind_retry_without_idempotency(retried, body_param="request_id")) == 1


def test_body_param_reroll_per_attempt_is_a_violation():
    """The pathology a body-param contract exists to forbid: a fresh token on
    every attempt, so the vendor treats the retry as a brand-new write.

    Before the grouping fix (2026-08-01), the differing `request_id` changed the
    body, the two attempts landed in DIFFERENT retry groups, and the connector
    escaped detection entirely — the one behaviour the check is for was the one
    it could not see. `retried_write_groups` now excludes the declared param from
    the grouping key, so the attempts group and the token mismatch is caught."""
    rerolled = [
        {
            "ts": 0,
            "method": "POST",
            "path": "/api/rpc",
            "body": {"op": "x", "request_id": "r1"},
            "status": 500,
            "idempotency_key": None,
        },
        {
            "ts": 1,
            "method": "POST",
            "path": "/api/rpc",
            "body": {"op": "x", "request_id": "r2"},
            "status": 200,
            "idempotency_key": None,
        },
    ]
    assert len(logs.retried_write_groups(rerolled, body_param="request_id")) == 1
    violations = logs.blind_retry_without_idempotency(rerolled, body_param="request_id")
    assert len(violations) == 1
    assert violations[0]["ts"] == 1
    # Evidence slice without the declaration: the attempts never even group, which
    # is why the check silently skipped instead of firing.
    assert logs.retried_write_groups(rerolled) == []


def _oc_retry(second_if_match: str) -> list[dict]:
    """Two PATCH attempts with an IDENTICAL body (the field change) — the only
    difference is the precondition header, which the (method, path, body) dedup
    key cannot see. This is onboardly's packet PATCH after a 409."""
    body = {"status": "countersigned"}
    return [
        {
            "ts": 0,
            "method": "PATCH",
            "path": "/v1/packets/pkt_1",
            "body": body,
            "status": 409,
            "idempotency_key": None,
            "headers": {"if-match": "3"},
        },
        {
            "ts": 1,
            "method": "PATCH",
            "path": "/v1/packets/pkt_1",
            "body": body,
            "status": 200,
            "idempotency_key": None,
            "headers": {"if-match": second_if_match},
        },
    ]


def test_informed_version_corrected_retry_is_not_blind():
    """A 409 told the connector its version was stale; it re-read and resubmitted
    against the current one. Correct behaviour — and unsatisfiable before this
    exemption existed (measured on task-0031: gold failed it in both scenarios)."""
    informed = _oc_retry("4")
    assert len(logs.retried_write_groups(informed)) == 1  # the slice is real
    assert logs.blind_retry_without_idempotency(informed, version_header="If-Match") == []
    # Without the declaration, gold's own correct retry reads as a violation.
    assert len(logs.blind_retry_without_idempotency(informed)) == 1


def test_retry_with_same_stale_version_is_still_blind():
    """Nothing was learned between attempts — the pathology worth flagging."""
    uninformed = _oc_retry("3")
    violations = logs.blind_retry_without_idempotency(uninformed, version_header="If-Match")
    assert len(violations) == 1
    assert violations[0]["ts"] == 1


def test_version_header_lookup_is_case_insensitive():
    """Declarations spell it `If-Match`; Starlette lowercases captured headers."""
    assert logs.header_value({"headers": {"if-match": "7"}}, "If-Match") == "7"
    assert logs.header_value({"headers": {"If-Match": "7"}}, "if-match") == "7"
    assert logs.header_value({"body": {}}, "If-Match") is None


def test_header_style_grouping_and_verdicts_are_unchanged():
    """Guard on the asymmetry: the same-token rule is scoped to body_param
    vendors so header-style tasks' measured baselines cannot shift underneath
    them. Two retries with DIFFERING header keys still read as compliant."""
    log = load("blind_retry.json")
    assert len(logs.blind_retry_without_idempotency(log)) == 1
    differing_headers = [
        {
            "ts": 0,
            "method": "POST",
            "path": "/rest/x",
            "body": {"op": "x"},
            "status": 500,
            "idempotency_key": "k1",
        },
        {
            "ts": 1,
            "method": "POST",
            "path": "/rest/x",
            "body": {"op": "x"},
            "status": 200,
            "idempotency_key": "k2",
        },
    ]
    assert logs.blind_retry_without_idempotency(differing_headers) == []
