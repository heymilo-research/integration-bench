"""`await builtin_l2(ctx)` — L2 hard gates and soft checks.

Reads the vendor request log and token log from the file-and-env log volume.

TRAFFIC-CONDITIONAL SEMANTICS: every check here is a prohibition, and a
prohibition proven by silence is no proof at all — an empty request log
"passes" all of them, which paid do-nothing submissions up to 0.97 of the
dense reward (WORKLOG 2026-07-26/29, measured suite-wide). Each check is
therefore recorded ONLY when its own evidence slice is non-empty; with the
reward's denominator fixed to the gold verdict's check set, a skipped check
counts as not-passed, so silence stops banking credit. The slice must be the
check's OWN evidence (the traffic that makes the prohibition meaningful),
never "any traffic at all" — see each check's slice below.
"""

from __future__ import annotations

from typing import Any

from bench.config import VendorMetadata
from bench.verifier import logs

DEFAULT_HOT_LOOP_K = 5
# Gap above which identical consecutive requests are separate attempts rather
# than one burst. See logs.hot_loop_violations: without it the check flagged
# correctly-backed-off retries, and task-0022 was tuned around that bug.
DEFAULT_HOT_LOOP_WINDOW_S = logs.DEFAULT_HOT_LOOP_WINDOW_S
DEFAULT_REAUTH_MARGIN = 3.0


def _detail(violations: list[dict], limit: int = 3) -> str:
    if not violations:
        return "no violations"
    sample = [{"path": v.get("path"), "event_id": v.get("event_id")} for v in violations[:limit]]
    return f"{len(violations)} violation(s); sample={sample}"


def _token_endpoints(vendor: VendorMetadata) -> tuple[str, ...]:
    """Every AUTH path, not just the declared token endpoint.

    These paths are excluded from the credential gates (they carry credentials
    legitimately) and from the blind-retry check (re-minting on a later pass is
    correct behaviour, not a non-idempotent write retry). Multi-step auth broke
    that: TalentForge exchanges at `/oauth/token` and THEN at `/rest/login`, so
    a legitimate re-auth showed up as two identical `POST /rest/login {}` bodies
    and was flagged as a blind retry — measured 2026-08-01, it failed
    task-0005's GOLD. So collect the declared token endpoint AND any additional
    step paths declared under `auth.routes[].steps[]` /
    `auth.additional_endpoints`, plus a small set of conventional login paths.
    """
    paths: list[str] = []
    if vendor.token_endpoint:
        paths.append(vendor.token_endpoint)

    raw = vendor.raw or {}
    auth = raw.get("auth") or {}
    for key in ("additional_endpoints", "endpoints", "step_endpoints"):
        extra = auth.get(key)
        if isinstance(extra, str):
            paths.append(extra)
        elif isinstance(extra, (list, tuple)):
            paths.extend(str(p) for p in extra if p)
    for route in auth.get("routes") or []:
        if not isinstance(route, dict):
            continue
        for step in route.get("steps") or []:
            if isinstance(step, str):
                paths.append(step)
            elif isinstance(step, dict):
                # Auth schemas name their routes by role as well as with the
                # generic endpoint/path keys.  An authorization-code step can
                # legitimately declare BOTH endpoints in the same mapping, so
                # collect every recognised key rather than stopping at the
                # first one.  Missing ``authorize_endpoint`` here made a
                # documented OAuth ``client_id`` query look like a data-plane
                # credential leak (task-0017).
                for k in (
                    "path",
                    "endpoint",
                    "url",
                    "authorize_endpoint",
                    "token_endpoint",
                    "login_endpoint",
                    "exchange_endpoint",
                ):
                    if step.get(k):
                        paths.append(str(step[k]))
    # Conventional second-step login paths some vendors expose without
    # declaring them; harmless to exclude since no task grades writes there.
    for conventional in (
        "/oauth/authorize",
        "/rest/login",
        "/login",
        "/session",
        "/auth/login",
    ):
        if conventional not in paths:
            paths.append(conventional)
    return tuple(dict.fromkeys(paths))


def _placement_query_params(vendor_raw: dict[str, Any]) -> tuple[str, ...]:
    params: list[str] = []
    for route in (vendor_raw.get("auth", {}) or {}).get("routes", []) or []:
        placement = route.get("placement", {}) or {}
        if "query" in placement:
            params.append(placement["query"])
    return tuple(params)


def _route_ttl(vendor: VendorMetadata) -> float | None:
    return vendor.token_ttl


def _log_window(entries: list[dict[str, Any]]) -> float:
    ts_values = [e.get("ts", 0) for e in entries if "ts" in e]
    if not ts_values:
        return 0.0
    return max(ts_values) - min(ts_values)


def _status(entry: dict[str, Any]) -> int:
    try:
        return int(entry.get("status", 200))
    except (TypeError, ValueError):
        return 200


async def builtin_l2(
    ctx,
    *,
    skip_requests: int = 0,
    exclude_request_indices=(),
    exclude_token_indices=(),
    app_runs: int = 1,
) -> None:
    """Grade the shared conduct rulebook over the vendor's own logs.

    VERIFIER-INJECTED TRAFFIC (measured 2026-08-01, task-0007): the request log
    records EVERY request the vendor served, including ones the SCENARIO made
    itself — probes that age a cursor, drain a webhook queue, or read state
    between phases. Those are not the submission's conduct, but they were graded
    as if they were: task-0007's verifier issued 8 identical bare list requests
    to age a cursor and `no_hot_loop_on_error` duly flagged 3 violations against
    GOLD, producing a false gold failure that took a full diagnosis to unwind.

    Two ways to exclude that traffic, both opt-in so existing callers are
    unaffected:
      * ``skip_requests=n`` — ignore the first n entries (snapshot
        ``len(vendor.request_log())`` before probing, pass it here).
      * ``exclude_request_indices=[...]`` — ignore specific 0-based indices,
        for probes interleaved with the connector's own traffic.
      * ``exclude_token_indices=[...]`` — ignore verifier-owned token mints.
        A verifier that authenticates its own control-plane probe otherwise
        makes a do-nothing connector appear to have healthy re-auth conduct.
    Prefer scoring conduct in a scenario that injects nothing at all; use these
    when that is not possible.
    """
    vendor = ctx.vendor_metadata
    vendor_handle = ctx.vendor(vendor.name)
    request_log = vendor_handle.request_log()
    if skip_requests or exclude_request_indices:
        excluded = set(exclude_request_indices)
        request_log = [
            entry for i, entry in enumerate(request_log) if i >= skip_requests and i not in excluded
        ]
    token_log = vendor_handle.token_log()
    if exclude_token_indices:
        excluded_tokens = set(exclude_token_indices)
        token_log = [entry for i, entry in enumerate(token_log) if i not in excluded_tokens]
    webhook_deliveries = vendor_handle.webhook_deliveries()

    secret_values = [v for v in ctx.secrets.values() if v]

    allowed_query_params = _placement_query_params(vendor.raw)
    mint_paths = _token_endpoints(vendor)

    # Slice for gates 1 and 4: data-plane requests (mint endpoints carry
    # credentials legitimately and are excluded from both checks anyway).
    #
    # HEALTHCHECK EXCLUSION (measured 2026-08-01, task-0032): every vendor's
    # compose healthcheck polls `GET /` every few seconds and the vendor logs it
    # unconditionally, so a submission that makes NO requests of its own still
    # produced a non-empty data-plane slice — which recorded both credential
    # gates and banked them, defeating the traffic-conditional rule for the two
    # checks most likely to pass vacuously. The connector never calls `/`
    # (it is the unauthenticated liveness endpoint, not part of any documented
    # API surface), so excluding it costs no real coverage.
    _NON_EVIDENCE_PATHS = {"/", ""}
    data_plane = [
        e
        for e in request_log
        if e.get("path") not in set(mint_paths) and e.get("path") not in _NON_EVIDENCE_PATHS
    ]

    if data_plane:
        violations = logs.creds_in_query(
            request_log, secret_values, allowed_query_params, exclude_paths=mint_paths
        )
        ctx.check_hard(
            "no_credentials_in_query_string",
            len(violations) == 0,
            _detail(violations),
        )

        violations = logs.secrets_echoed_wrong_placement(request_log, secret_values, mint_paths)
        ctx.check_hard(
            "no_secrets_echoed_to_vendor",
            len(violations) == 0,
            _detail(violations),
        )

    webhooks_cfg = (vendor.raw.get("webhooks") if vendor.raw else None) or {}
    if webhooks_cfg.get("enabled"):
        # Slice: bad deliveries a live listener actually answered. A dead or
        # deaf listener (response_code null) is not evidence of rejection —
        # "rejects tampered payloads" cannot be proven by being unreachable.
        responded_tampered = [
            d
            for d in webhook_deliveries
            if d.get("tampered") and d.get("response_code") is not None
        ]
        if responded_tampered:
            violations = logs.webhook_bad_signature_accepted(responded_tampered)
            ctx.check_hard(
                "webhook_bad_signature_rejected",
                len(violations) == 0,
                _detail(violations),
            )

        max_skew_s = (webhooks_cfg.get("signing", {}) or {}).get("max_skew_s", 300)
        responded_stale = [
            d
            for d in webhook_deliveries
            if int(d.get("skew_s") or 0) > max_skew_s and d.get("response_code") is not None
        ]
        if responded_stale:
            violations = logs.webhook_stale_timestamp_accepted(responded_stale, max_skew_s)
            ctx.check_hard(
                "webhook_stale_timestamp_rejected",
                len(violations) == 0,
                _detail(violations),
            )

    window_s = _log_window(request_log or token_log)
    ttl_s = _route_ttl(vendor)
    if ttl_s is not None:
        actual, allowed_max = logs.excess_token_mints(
            token_log, ttl_s, window_s, margin=DEFAULT_REAUTH_MARGIN, route=vendor.token_endpoint
        )
        # The ceiling assumes ONE continuous session paced by the token TTL. A
        # scenario that deliberately launches N independent one-shot `sync`
        # processes breaks that: each process starts with an empty in-memory
        # token cache and MUST mint at least once, so N runs legitimately need
        # >= N mints. Measured 2026-08-01 on task-0003, whose scenario fires 30
        # one-shot passes to trip a 20-req/60s token limiter — tripping the
        # limiter REQUIRES ~30 mints while the unscaled ceiling was ~10, making
        # the check mathematically UNSATISFIABLE for any correct connector and
        # failing gold. Callers that drive the app repeatedly pass app_runs=N.
        if app_runs > 1:
            allowed_max = allowed_max * app_runs
        # Slice: at least one mint on this route.
        if actual > 0:
            ctx.check_soft(
                f"reauth_per_request:{vendor.token_endpoint or 'token'}",
                actual <= allowed_max,
                f"{actual} mint(s), generous ceiling={allowed_max}",
            )

    # Slice: a Retry-After was actually served.
    rate_limited_entries = [
        e for e in request_log if e.get("rate_limited") and e.get("retry_after") is not None
    ]
    if rate_limited_entries:
        violations = logs.retry_after_violations(request_log)
        ctx.check_soft(
            "retry_after_honored",
            len(violations) == 0,
            _detail(violations),
        )

    vendor_raw = vendor.raw
    base_path = (vendor_raw.get("vendor", {}) or {}).get("base_path", "")
    pagination = vendor_raw.get("pagination", {}) or {}
    cursor_param = pagination.get("cursor_param", "cursor")
    offset_param = pagination.get("offset_param", "offset")

    # A full-resync violation means "listed a collection with NO incremental
    # narrowing after having already used some". Only a WATERMARK/FRESHNESS
    # param counts as narrowing.
    #
    # A PAGINATION CURSOR MUST NOT COUNT (measured 2026-08-01, task-0034): a
    # cursor is mid-crawl paging, not narrowing. On any collection that needs
    # more than one page, the cursor appears on page 2 of the very FIRST crawl,
    # which latches `seen_incremental` for the rest of the vendor epoch — after
    # which every later, legitimately cheap unparameterised poll (e.g. a
    # conditional GET the connector re-issues while holding a matching ETag) is
    # flagged as a bogus full resync. That cost task-0034's GOLD five false
    # violations. The old default `("modified_since", "cursor")` had this bug
    # and adding each vendor's `cursor_param` amplified it; both are dropped.
    #
    # Vendor-specific watermark names are still honoured, either from the
    # freshness aliases below or from an explicit
    # `pagination.incremental_params` in task.yaml (sourcewell's is `since`).
    declared = pagination.get("incremental_params") or pagination.get("incremental_param")
    if isinstance(declared, str):
        declared = [declared]
    incremental_params = tuple(
        dict.fromkeys(
            [
                "modified_since",
                "since",
                "updated_since",
                "modified_after",
                "updated_after",
                *(declared or []),
            ]
        )
    )

    for entity_name, edef in (vendor_raw.get("entities") or {}).items():
        plural = (edef or {}).get("plural")
        if not plural:
            raise ValueError(
                f"entity {entity_name!r} is missing required 'plural:' field in vendor metadata"
            )
        list_path = f"{base_path}/{plural}"
        entity_entries = [e for e in request_log if e.get("path") == list_path]
        detail_prefix = list_path.rstrip("/") + "/"
        entity_detail_entries = [
            e
            for e in request_log
            if e.get("method") == "GET"
            and str(e.get("path") or "").startswith(detail_prefix)
            and "/" not in str(e.get("path") or "")[len(detail_prefix) :]
        ]

        # Slice: this entity was read through either supported strategy.  A
        # targeted GET-by-id implementation is positive evidence that the
        # connector avoided an unnecessary collection resync; requiring a
        # collection LIST merely to emit this check penalised the more focused
        # strategy (task-0026).  No traffic still emits no check, preserving the
        # anti-vacuity rule.
        if entity_entries or entity_detail_entries:
            violations = logs.full_resync_violations(
                request_log, list_path, incremental_params=incremental_params
            )
            ctx.check_soft(
                f"no_unnecessary_full_resync:{entity_name}",
                len(violations) == 0,
                (
                    _detail(violations)
                    if entity_entries
                    else f"no collection resync; {len(entity_detail_entries)} targeted read(s)"
                ),
            )

        # Slice: a mid-pagination failure happened on this collection —
        # resume-vs-restart is unobservable without one.
        if any(_status(e) >= 500 for e in entity_entries):
            violations = logs.resume_from_page1_violations(
                request_log, list_path, cursor_param=cursor_param, offset_param=offset_param
            )
            ctx.check_soft(
                f"resume_not_restart_on_retry:{entity_name}",
                len(violations) == 0,
                _detail(violations),
            )

    # Slice: at least one error response to react to.
    if any(_status(e) == 401 or _status(e) >= 500 for e in request_log):
        violations = logs.hot_loop_violations(
            request_log, k=DEFAULT_HOT_LOOP_K, window_s=DEFAULT_HOT_LOOP_WINDOW_S
        )
        ctx.check_soft(
            "no_hot_loop_on_error",
            len(violations) == 0,
            _detail(violations),
        )

    idempotency = (vendor_raw.get("writeback", {}) or {}).get("idempotency")
    if idempotency:
        # Honour the vendor's declared idempotency STYLE. A header-style vendor
        # surfaces its token as the log's `idempotency_key`; a body-param vendor
        # (paygrade's `request_id`) carries it in the body and leaves that field
        # null, which made a CORRECT retry read as blind and cost task-0043's
        # gold its own check (measured 2026-08-01). Declaring the param is the
        # supported fix — do NOT rename the metadata key to silence the check,
        # which removes the coverage instead of correcting it.
        body_param = idempotency.get("body_param") if isinstance(idempotency, dict) else None
        # Slice: at least one write was actually retried. `body_param` MUST be
        # threaded through here too — the slice and the violation set have to be
        # computed over the same grouping, or a body-param connector that re-rolls
        # its token per attempt reads as "no retries observed" and skips the very
        # check that would have caught it.
        # An optimistic-concurrency endpoint retries by resubmitting the SAME body
        # with a corrected precondition header, which the (method, path, body) dedup
        # key cannot see — so declare the header and let the informed retry be
        # recognised as such instead of counted as blind.
        version_header = (vendor_raw.get("optimistic_concurrency", {}) or {}).get("match_header")
        if logs.retried_write_groups(request_log, exclude_paths=mint_paths, body_param=body_param):
            violations = logs.blind_retry_without_idempotency(
                request_log,
                exclude_paths=mint_paths,
                body_param=body_param,
                version_header=version_header,
            )
            ctx.check_soft(
                "idempotent_write_retries",
                len(violations) == 0,
                _detail(violations),
            )
