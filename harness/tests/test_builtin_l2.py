"""Tests for bench.verifier.builtin_l2 wired against a fake vendor handle
(no docker/httpx needed) using the sample_task task.yaml."""

from pathlib import Path

import pytest

from bench.config import TaskConfig, VendorMetadata
from bench.verifier.builtin_l2 import builtin_l2

SAMPLE_TASK = Path(__file__).parent / "fixtures" / "sample_task"


def _vendor_metadata() -> VendorMetadata:
    task = TaskConfig.load(SAMPLE_TASK)
    return task.vendors["samplevendor"]


class FakeVendorHandle:
    def __init__(self, request_log, token_log, webhook_deliveries=None):
        self._request_log = request_log
        self._token_log = token_log
        self._webhook_deliveries = webhook_deliveries or []

    def request_log(self):
        return self._request_log

    def token_log(self):
        return self._token_log

    def webhook_deliveries(self):
        return self._webhook_deliveries


class FakeCtx:
    def __init__(
        self, vendor: VendorMetadata, secrets, request_log, token_log, webhook_deliveries=None
    ):
        self.vendor_metadata = vendor
        self.secrets = secrets
        self._handle = FakeVendorHandle(request_log, token_log, webhook_deliveries)
        self.hard_checks: list[tuple[str, bool, str]] = []
        self.soft_checks: list[tuple[str, bool, str]] = []

    def vendor(self, name: str):
        return self._handle

    def check_l1(self, *a, **k):
        pass

    def check_l3(self, *a, **k):
        pass

    def check_hard(self, name, ok, detail=""):
        self.hard_checks.append((name, ok, detail))

    def check_soft(self, name, ok, detail=""):
        self.soft_checks.append((name, ok, detail))


SECRETS = {
    "SV_CLIENT_ID": "sample-client-id",
    "SV_CLIENT_SECRET": "sample-client-secret",
    "SV_WEBHOOK_SECRET": "sample-webhook-secret",
}


@pytest.mark.asyncio
async def test_builtin_l2_clean_run_all_checks_pass():
    request_log = [
        {
            "ts": 0,
            "method": "GET",
            "path": "/rest/candidates",
            "query": {"offset": "0", "limit": "50"},
            "body": None,
            "status": 200,
            "rate_limited": False,
        },
        {
            "ts": 1,
            "method": "POST",
            "path": "/oauth/token",
            "query": {},
            "body": {"client_id": "sample-client-id", "client_secret": "sample-client-secret"},
            "status": 200,
            "rate_limited": False,
        },
    ]
    webhook_deliveries = [
        {"event_id": "evt_1", "response_code": 200, "tampered": False, "skew_s": 0, "ts": 0},
    ]
    token_log = [{"ts": 0, "kind": "access_token", "route": "client_creds", "expires_in": 600}]

    ctx = FakeCtx(_vendor_metadata(), SECRETS, request_log, token_log, webhook_deliveries)
    await builtin_l2(ctx)

    assert ctx.hard_checks, "expected hard gate checks to be recorded"
    assert all(ok for _, ok, _ in ctx.hard_checks), ctx.hard_checks
    assert ctx.soft_checks, "expected soft checks to be recorded"
    assert all(ok for _, ok, _ in ctx.soft_checks), ctx.soft_checks


@pytest.mark.asyncio
async def test_builtin_l2_flags_credential_in_query():
    request_log = [
        {
            "ts": 0,
            "method": "GET",
            "path": "/rest/candidates",
            "query": {"token": "sample-client-secret"},
            "body": None,
            "status": 200,
            "rate_limited": False,
        },
    ]
    ctx = FakeCtx(_vendor_metadata(), SECRETS, request_log, [], [])
    await builtin_l2(ctx)
    hard = dict((n, ok) for n, ok, _ in ctx.hard_checks)
    assert hard["no_credentials_in_query_string"] is False


@pytest.mark.asyncio
async def test_builtin_l2_allows_client_id_on_declared_authorize_endpoint():
    """Role-named auth endpoints are mint paths, not data-plane leaks."""
    vendor = _vendor_metadata()
    vendor.raw["auth"] = {
        "routes": [
            {
                "steps": [
                    {
                        "type": "authorization_code",
                        "authorize_endpoint": "/oauth/authorize",
                        "token_endpoint": "/oauth/token",
                    }
                ]
            }
        ]
    }
    request_log = [
        {
            "ts": 0,
            "method": "GET",
            "path": "/oauth/authorize",
            "query": {"client_id": "sample-client-id", "response_type": "code"},
            "status": 200,
        },
        {
            "ts": 1,
            "method": "GET",
            "path": "/rest/candidates",
            "query": {},
            "status": 200,
        },
    ]
    ctx = FakeCtx(vendor, SECRETS, request_log, [], [])
    await builtin_l2(ctx)
    hard = dict((name, ok) for name, ok, _ in ctx.hard_checks)
    assert hard["no_credentials_in_query_string"] is True
    assert hard["no_secrets_echoed_to_vendor"] is True


@pytest.mark.asyncio
async def test_builtin_l2_allows_standard_oauth_authorize_path_with_compact_metadata():
    """Task-local metadata may retain token_endpoint but omit vendor auth steps."""
    vendor = _vendor_metadata()
    vendor.raw["auth"] = {"token_endpoint": "/oauth/token", "token_ttl": 600}
    request_log = [
        {
            "ts": 0,
            "method": "GET",
            "path": "/oauth/authorize",
            "query": {"client_id": "sample-client-id", "response_type": "code"},
            "status": 200,
        },
        {
            "ts": 1,
            "method": "GET",
            "path": "/rest/candidates",
            "query": {},
            "status": 200,
        },
    ]
    ctx = FakeCtx(vendor, SECRETS, request_log, [], [])
    await builtin_l2(ctx)
    hard = dict((name, ok) for name, ok, _ in ctx.hard_checks)
    assert hard["no_credentials_in_query_string"] is True
    assert hard["no_secrets_echoed_to_vendor"] is True


@pytest.mark.asyncio
async def test_builtin_l2_flags_webhook_bad_signature_accepted():
    webhook_deliveries = [
        {"event_id": "evt_bad", "response_code": 200, "tampered": True, "skew_s": 0, "ts": 0},
    ]
    ctx = FakeCtx(_vendor_metadata(), SECRETS, [], [], webhook_deliveries)
    await builtin_l2(ctx)
    hard = dict((n, ok) for n, ok, _ in ctx.hard_checks)
    assert hard["webhook_bad_signature_rejected"] is False


@pytest.mark.asyncio
async def test_builtin_l2_flags_hot_loop():
    request_log = [
        {
            "ts": t,
            "method": "GET",
            "path": "/rest/candidates",
            "query": {"offset": "0"},
            "status": 401,
            "rate_limited": False,
        }
        for t in range(8)
    ]
    ctx = FakeCtx(_vendor_metadata(), SECRETS, request_log, [], [])
    await builtin_l2(ctx)
    soft = dict((n, ok) for n, ok, _ in ctx.soft_checks)
    assert soft["no_hot_loop_on_error"] is False


# --- traffic-conditional (skip-on-empty-evidence) semantics -----------------


@pytest.mark.asyncio
async def test_builtin_l2_empty_logs_record_no_checks_at_all():
    """A do-nothing submission must bank nothing: every prohibition skips."""
    ctx = FakeCtx(_vendor_metadata(), SECRETS, [], [], [])
    await builtin_l2(ctx)
    assert ctx.hard_checks == []
    assert ctx.soft_checks == []


@pytest.mark.asyncio
async def test_builtin_l2_deaf_listener_does_not_bank_webhook_gates():
    """Tampered/stale deliveries the listener never answered (response_code
    null) are not evidence of rejection — both webhook gates must skip."""
    webhook_deliveries = [
        {"event_id": "evt_bad", "response_code": None, "tampered": True, "skew_s": 0, "ts": 0},
        {"event_id": "evt_old", "response_code": None, "tampered": False, "skew_s": 9999, "ts": 1},
    ]
    ctx = FakeCtx(_vendor_metadata(), SECRETS, [], [], webhook_deliveries)
    await builtin_l2(ctx)
    names = [n for n, _, _ in ctx.hard_checks]
    assert "webhook_bad_signature_rejected" not in names
    assert "webhook_stale_timestamp_rejected" not in names


@pytest.mark.asyncio
async def test_builtin_l2_live_listener_rejecting_tamper_records_pass():
    webhook_deliveries = [
        {"event_id": "evt_bad", "response_code": 401, "tampered": True, "skew_s": 0, "ts": 0},
        {"event_id": "evt_old", "response_code": 401, "tampered": False, "skew_s": 9999, "ts": 1},
    ]
    ctx = FakeCtx(_vendor_metadata(), SECRETS, [], [], webhook_deliveries)
    await builtin_l2(ctx)
    hard = dict((n, ok) for n, ok, _ in ctx.hard_checks)
    assert hard["webhook_bad_signature_rejected"] is True
    assert hard["webhook_stale_timestamp_rejected"] is True


@pytest.mark.asyncio
async def test_builtin_l2_quiet_run_skips_unobservable_soft_checks():
    """One clean data-plane GET: creds/secrets gates and full_resync record;
    checks whose evidence never happened (429s, 5xxs, retries, mints) skip."""
    request_log = [
        {
            "ts": 0,
            "method": "GET",
            "path": "/rest/candidates",
            "query": {"offset": "0", "limit": "50"},
            "body": None,
            "status": 200,
            "rate_limited": False,
        },
    ]
    ctx = FakeCtx(_vendor_metadata(), SECRETS, request_log, [], [])
    await builtin_l2(ctx)
    hard_names = [n for n, _, _ in ctx.hard_checks]
    assert "no_credentials_in_query_string" in hard_names
    assert "no_secrets_echoed_to_vendor" in hard_names
    soft_names = [n for n, _, _ in ctx.soft_checks]
    assert "no_unnecessary_full_resync:candidate" in soft_names or any(
        n.startswith("no_unnecessary_full_resync:") for n in soft_names
    )
    assert "retry_after_honored" not in soft_names
    assert "no_hot_loop_on_error" not in soft_names
    assert not any(n.startswith("resume_not_restart_on_retry:") for n in soft_names)
    assert not any(n.startswith("reauth_per_request:") for n in soft_names)
    assert "idempotent_write_retries" not in soft_names


@pytest.mark.asyncio
async def test_builtin_l2_entity_without_traffic_skips_only_that_entity():
    """Per-entity checks are sliced per entity: listing one collection must
    not record full_resync checks for collections never touched."""
    vendor = _vendor_metadata()
    entities = vendor.raw.get("entities") or {}
    assert len(entities) >= 1
    first_entity = next(iter(entities))
    plural = entities[first_entity]["plural"]
    base_path = (vendor.raw.get("vendor", {}) or {}).get("base_path", "")
    request_log = [
        {
            "ts": 0,
            "method": "GET",
            "path": f"{base_path}/{plural}",
            "query": {},
            "body": None,
            "status": 200,
            "rate_limited": False,
        },
    ]
    ctx = FakeCtx(vendor, SECRETS, request_log, [], [])
    await builtin_l2(ctx)
    soft_names = [n for n, _, _ in ctx.soft_checks]
    assert f"no_unnecessary_full_resync:{first_entity}" in soft_names
    for other in entities:
        if other != first_entity:
            assert f"no_unnecessary_full_resync:{other}" not in soft_names


@pytest.mark.asyncio
async def test_builtin_l2_targeted_detail_reads_prove_no_full_resync():
    vendor = _vendor_metadata()
    entities = vendor.raw.get("entities") or {}
    first_entity = next(iter(entities))
    plural = entities[first_entity]["plural"]
    base_path = (vendor.raw.get("vendor", {}) or {}).get("base_path", "")
    request_log = [
        {
            "ts": 0,
            "method": "GET",
            "path": f"{base_path}/{plural}/record-1",
            "query": {},
            "status": 200,
        }
    ]
    ctx = FakeCtx(vendor, SECRETS, request_log, [], [])
    await builtin_l2(ctx)
    soft = {name: (ok, detail) for name, ok, detail in ctx.soft_checks}
    assert soft[f"no_unnecessary_full_resync:{first_entity}"][0] is True
    assert "targeted read" in soft[f"no_unnecessary_full_resync:{first_entity}"][1]


@pytest.mark.asyncio
async def test_builtin_l2_retry_after_recorded_only_when_429_served():
    request_log = [
        {
            "ts": 0,
            "method": "GET",
            "path": "/rest/candidates",
            "query": {},
            "status": 429,
            "rate_limited": True,
            "retry_after": 5,
        },
        {
            "ts": 1,
            "method": "GET",
            "path": "/rest/candidates",
            "query": {},
            "status": 200,
            "rate_limited": False,
        },
    ]
    ctx = FakeCtx(_vendor_metadata(), SECRETS, request_log, [], [])
    await builtin_l2(ctx)
    soft = dict((n, ok) for n, ok, _ in ctx.soft_checks)
    assert soft["retry_after_honored"] is False  # retried after 1s against a 5s Retry-After


@pytest.mark.asyncio
async def test_builtin_l2_vendor_specific_incremental_param_not_full_resync():
    """A vendor whose watermark param is not the generic 'modified_since' must
    not read as a full re-sync (measured on sourcewell's `since`, 2026-08-01)."""
    vendor = _vendor_metadata()
    entities = vendor.raw.get("entities") or {}
    first_entity = next(iter(entities))
    plural = entities[first_entity]["plural"]
    base_path = (vendor.raw.get("vendor", {}) or {}).get("base_path", "")
    list_path = f"{base_path}/{plural}"
    vendor.raw.setdefault("pagination", {})["incremental_params"] = ["since"]

    request_log = [
        {
            "ts": 0,
            "method": "GET",
            "path": list_path,
            "query": {"since": "1"},
            "body": None,
            "status": 200,
            "rate_limited": False,
        },
        {
            "ts": 1,
            "method": "GET",
            "path": list_path,
            "query": {"since": "2"},
            "body": None,
            "status": 200,
            "rate_limited": False,
        },
    ]
    ctx = FakeCtx(vendor, SECRETS, request_log, [], [])
    await builtin_l2(ctx)
    soft = dict((n, ok) for n, ok, _ in ctx.soft_checks)
    assert soft[f"no_unnecessary_full_resync:{first_entity}"] is True


@pytest.mark.asyncio
async def test_builtin_l2_healthcheck_traffic_is_not_evidence():
    """The compose healthcheck's `GET /` is logged by every vendor, but it is
    not the submission's traffic — it must not make the credential gates
    record (measured 2026-08-01 on task-0032: it was banking 2 checks per
    builtin_l2 invocation for a do-nothing stub)."""
    request_log = [
        {
            "ts": 0,
            "method": "GET",
            "path": "/",
            "query": {},
            "body": None,
            "status": 200,
            "rate_limited": False,
        },
        {
            "ts": 1,
            "method": "GET",
            "path": "/",
            "query": {},
            "body": None,
            "status": 200,
            "rate_limited": False,
        },
    ]
    ctx = FakeCtx(_vendor_metadata(), SECRETS, request_log, [], [])
    await builtin_l2(ctx)
    names = [n for n, _, _ in ctx.hard_checks]
    assert "no_credentials_in_query_string" not in names
    assert "no_secrets_echoed_to_vendor" not in names


@pytest.mark.asyncio
async def test_builtin_l2_skip_requests_excludes_verifier_probes():
    """Verifier-injected probes must not be graded as connector conduct.

    task-0007 (2026-08-01) aged a cursor with 8 identical bare list requests and
    `no_hot_loop_on_error` flagged them against GOLD — a false gold failure.
    """
    probes = [
        {
            "ts": t,
            "method": "GET",
            "path": "/rest/candidates",
            "query": {"offset": "0"},
            "status": 500,
            "rate_limited": False,
        }
        for t in range(8)
    ]
    connector = [
        {
            "ts": 100,
            "method": "GET",
            "path": "/rest/candidates",
            "query": {"offset": "0", "limit": "50"},
            "body": None,
            "status": 200,
            "rate_limited": False,
        },
    ]
    # Ungated: the probes look like a hot loop and fail the check.
    ctx = FakeCtx(_vendor_metadata(), SECRETS, probes + connector, [], [])
    await builtin_l2(ctx)
    assert dict((n, ok) for n, ok, _ in ctx.soft_checks)["no_hot_loop_on_error"] is False

    # Skipping the probe prefix: only the connector's own traffic is graded.
    ctx2 = FakeCtx(_vendor_metadata(), SECRETS, probes + connector, [], [])
    await builtin_l2(ctx2, skip_requests=len(probes))
    soft2 = dict((n, ok) for n, ok, _ in ctx2.soft_checks)
    assert "no_hot_loop_on_error" not in soft2 or soft2["no_hot_loop_on_error"] is True


@pytest.mark.asyncio
async def test_builtin_l2_exclude_specific_indices():
    log = [
        {
            "ts": 0,
            "method": "GET",
            "path": "/rest/candidates",
            "query": {"token": "sample-client-secret"},
            "body": None,
            "status": 200,
            "rate_limited": False,
        },
        {
            "ts": 1,
            "method": "GET",
            "path": "/rest/candidates",
            "query": {"offset": "0"},
            "body": None,
            "status": 200,
            "rate_limited": False,
        },
    ]
    ctx = FakeCtx(_vendor_metadata(), SECRETS, log, [], [])
    await builtin_l2(ctx, exclude_request_indices=[0])
    hard = dict((n, ok) for n, ok, _ in ctx.hard_checks)
    assert hard["no_credentials_in_query_string"] is True


@pytest.mark.asyncio
async def test_builtin_l2_exclude_verifier_token_indices():
    """A verifier-owned token mint is not connector re-auth evidence."""
    token_log = [
        {"ts": 0, "kind": "access_token", "route": "/oauth/token", "expires_in": 600},
    ]
    ctx = FakeCtx(_vendor_metadata(), SECRETS, [], token_log, [])
    await builtin_l2(ctx, exclude_token_indices=[0])
    names = [name for name, _ok, _detail in ctx.soft_checks]
    assert not any(name.startswith("reauth_per_request:") for name in names)


@pytest.mark.asyncio
async def test_builtin_l2_pagination_cursor_is_not_narrowing_evidence():
    """A cursor is paging, not a freshness filter.

    Measured 2026-08-01 on task-0034: a collection needing >1 page emits a
    cursor on page 2 of the FIRST crawl; if that counts as narrowing it latches
    for the rest of the epoch and every later unparameterised poll is flagged a
    full resync — five false violations against GOLD.
    """
    vendor = _vendor_metadata()
    entities = vendor.raw.get("entities") or {}
    first_entity = next(iter(entities))
    plural = entities[first_entity]["plural"]
    base_path = (vendor.raw.get("vendor", {}) or {}).get("base_path", "")
    list_path = f"{base_path}/{plural}"

    request_log = [
        # First crawl: page 1 then page 2 via cursor (pure paging).
        {
            "ts": 0,
            "method": "GET",
            "path": list_path,
            "query": {},
            "body": None,
            "status": 200,
            "rate_limited": False,
        },
        {
            "ts": 1,
            "method": "GET",
            "path": list_path,
            "query": {"cursor": "abc"},
            "body": None,
            "status": 200,
            "rate_limited": False,
        },
        # A later cheap poll with no params — legitimate, must NOT be a violation.
        {
            "ts": 2,
            "method": "GET",
            "path": list_path,
            "query": {},
            "body": None,
            "status": 304,
            "rate_limited": False,
        },
    ]
    ctx = FakeCtx(vendor, SECRETS, request_log, [], [])
    await builtin_l2(ctx)
    soft = dict((n, ok) for n, ok, _ in ctx.soft_checks)
    assert soft[f"no_unnecessary_full_resync:{first_entity}"] is True


@pytest.mark.asyncio
async def test_builtin_l2_app_runs_scales_the_reauth_ceiling():
    """N independent one-shot processes must each mint at least once.

    task-0003 (2026-08-01) fires 30 one-shot passes to trip a 20-req/60s token
    limiter; the unscaled ceiling was ~10, so the check was UNSATISFIABLE for a
    correct connector and failed gold.
    """
    vendor = _vendor_metadata()
    token_log = [
        {"ts": t, "kind": "access_token", "route": "/oauth/token", "expires_in": 3600}
        for t in range(30)
    ]
    request_log = [
        {
            "ts": 0,
            "method": "GET",
            "path": "/rest/candidates",
            "query": {},
            "body": None,
            "status": 200,
            "rate_limited": False,
        },
    ]
    ctx = FakeCtx(vendor, SECRETS, request_log, token_log, [])
    await builtin_l2(ctx)
    name = f"reauth_per_request:{vendor.token_endpoint or 'token'}"
    assert dict((n, ok) for n, ok, _ in ctx.soft_checks)[name] is False

    ctx2 = FakeCtx(vendor, SECRETS, request_log, token_log, [])
    await builtin_l2(ctx2, app_runs=30)
    assert dict((n, ok) for n, ok, _ in ctx2.soft_checks)[name] is True


@pytest.mark.asyncio
async def test_builtin_l2_multistep_auth_reauth_is_not_a_blind_retry():
    """A second auth step is auth, not a write.

    TalentForge exchanges at /oauth/token then /rest/login; a legitimate re-auth
    produced two identical `POST /rest/login {}` bodies and was flagged as a
    non-idempotent write retry, failing task-0005's GOLD (2026-08-01).
    """
    vendor = _vendor_metadata()
    vendor.raw.setdefault("writeback", {})["idempotency"] = {"header": "Idempotency-Key"}
    request_log = [
        {
            "ts": 0,
            "method": "POST",
            "path": "/rest/login",
            "query": {},
            "body": {},
            "status": 200,
            "rate_limited": False,
            "idempotency_key": None,
        },
        {
            "ts": 1,
            "method": "GET",
            "path": "/rest/candidates",
            "query": {},
            "body": None,
            "status": 200,
            "rate_limited": False,
        },
        {
            "ts": 2,
            "method": "POST",
            "path": "/rest/login",
            "query": {},
            "body": {},
            "status": 200,
            "rate_limited": False,
            "idempotency_key": None,
        },
    ]
    ctx = FakeCtx(vendor, SECRETS, request_log, [], [])
    await builtin_l2(ctx)
    soft = dict((n, ok) for n, ok, _ in ctx.soft_checks)
    assert soft.get("idempotent_write_retries", True) is True
