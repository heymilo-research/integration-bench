"""Pure L2 log-analysis functions (conduct-rules.md hard gates + soft checks).

These take already-parsed JSON (as returned by ControlClient.request_log() /
.webhook_deliveries() / .tokens(), or loaded straight from hand-written fixture
JSON in tests) and return violation lists. They do no I/O and raise nothing on
malformed-but-plausible input, so they're unit-testable without docker/vendorsim.

CONTRACT ASSUMPTION — request-log entry shape (docs/vendorsim-config.md §11
says only: "ts, method, path, query, headers(redacted values, kept names),
auth outcome, status, rate-limit events"; the exact field names/nesting are
not pinned, so this harness assumes vendorsim's `/control/request-log` emits
one dict per request shaped like:

    {
      "ts": <float, seconds, monotonic within the run>,
      "method": "GET",
      "path": "/rest/candidates",
      "query": {"cursor": "...", "modified_since": "..."},   # full values (not redacted)
      "headers": {"Authorization": "***"},                    # names kept, VALUES redacted
      "body": {...} | null,                                   # parsed JSON body, full values
      "status": 200,
      "auth_outcome": "ok" | "expired" | "invalid" | "missing",
      "rate_limited": false,
      "retry_after": null | <int seconds>,                    # present when rate_limited
      "idempotency_key": null | "<value of Idempotency-Key header>"
    }

Because headers are documented as value-redacted, hard-gate checks that need
to see a *value* (credentials-in-query, secrets-echoed) rely on `query` and
`body`, which the doc's wording implies are NOT value-redacted (only headers
are called out). If vendorsim instead redacts those too, gates 1/4 would need
rework — flagged in harness/README.md.

CONTRACT ASSUMPTION — webhook-deliveries entry shape (docs §11 says: "event
id, attempt, target, response code"; harness additionally assumes vendorsim
annotates each delivery with whether it was a tamper-injected / skewed
delivery, since gate 2/3 require knowing which deliveries were intentionally
bad and whether the connector's response accepted them):

    {
      "event_id": "evt_00031",
      "attempt": 1,
      "target": "http://connector:4000/webhooks/talentforge",
      "response_code": 200,
      "ts": <float>,
      "tampered": false,     # true = sim sent a bad-HMAC payload (tamper_inject)
      "skew_s": 0            # timestamp skew injected on this delivery, seconds
    }

CONTRACT ASSUMPTION — token-log entry shape (docs §11: "kind, route, ts,
expiry"):

    {"ts": <float>, "kind": "access_token", "route": "hybrid_oauth_session",
     "expires_in": 600}
"""

from __future__ import annotations

from typing import Any

Entry = dict[str, Any]


def _accepted(status: int) -> bool:
    return 200 <= status < 300


# ---------------------------------------------------------------------------
# Hard gates (conduct-rules.md "Hard gates")
# ---------------------------------------------------------------------------


def creds_in_query(
    request_log: list[Entry],
    secret_values: list[str],
    allowed_query_params: tuple[str, ...] = (),
    exclude_paths: tuple[str, ...] = (),
) -> list[Entry]:
    """Hard gate 1: credential value present in a query string param, where
    that param is not the vendor's documented query-placement param.

    ``exclude_paths`` drops requests to auth *mint* endpoints (authorize / token /
    session-exchange). Those endpoints legitimately carry credentials in their
    documented way — notably an OAuth ``authorization_code`` flow puts ``client_id``
    (a public identifier, but still a configured ``*_env`` value) in the
    ``/oauth/authorize`` query string per the RFC and the vendor docs. Hard gate 1
    targets *data-plane* calls that leak the placed credential (session token /
    secret) into a query string instead of the documented header; a competent,
    fully-correct connector following the documented mint flow trips the naive
    version, so per the conduct-rules.md invariants-only litmus test those mint
    endpoints must be excluded. Data-plane requests remain fully checked."""
    secret_set = {v for v in secret_values if v}
    excluded = set(exclude_paths)
    violations = []
    for entry in request_log:
        if entry.get("path") in excluded:
            continue
        for param, value in (entry.get("query") or {}).items():
            if param in allowed_query_params:
                continue
            if str(value) in secret_set:
                violations.append(entry)
                break
    return violations


def secrets_echoed_wrong_placement(
    request_log: list[Entry],
    secret_values: list[str],
    allowed_paths: tuple[str, ...] = (),
) -> list[Entry]:
    """Hard gate 4: a secret value appears in a request path or body on an
    endpoint outside its documented allowed placement (e.g. client_secret
    sent to a data-plane endpoint instead of only the token endpoint)."""
    secret_set = {v for v in secret_values if v}
    violations = []
    for entry in request_log:
        path = entry.get("path", "")
        if path in allowed_paths:
            continue
        if any(v and v in path for v in secret_set):
            violations.append(entry)
            continue
        body = entry.get("body")
        if body and _contains_value(body, secret_set):
            violations.append(entry)
    return violations


def _contains_value(obj: Any, values: set[str]) -> bool:
    if isinstance(obj, dict):
        return any(_contains_value(v, values) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_value(v, values) for v in obj)
    return str(obj) in values


def webhook_bad_signature_accepted(webhook_deliveries: list[Entry]) -> list[Entry]:
    """Hard gate 2: tamper-injected (bad HMAC) payload was accepted (2xx)."""
    return [
        d
        for d in webhook_deliveries
        # response_code is null when the listener dropped the connection
        # without responding — that is not an acceptance.
        if d.get("tampered") and _accepted(int(d.get("response_code") or 0))
    ]


def webhook_stale_timestamp_accepted(
    webhook_deliveries: list[Entry], max_skew_s: int
) -> list[Entry]:
    """Hard gate 3: a payload beyond max_skew_s was accepted (2xx)."""
    return [
        d
        for d in webhook_deliveries
        if int(d.get("skew_s") or 0) > max_skew_s and _accepted(int(d.get("response_code") or 0))
    ]


# ---------------------------------------------------------------------------
# Soft checks (conduct-rules.md "Soft score")
# ---------------------------------------------------------------------------


def excess_token_mints(
    token_log: list[Entry],
    ttl_s: float,
    window_s: float,
    margin: float = 3.0,
    route: str | None = None,
) -> tuple[int, int]:
    """Soft rule 1: re-auth per request. Returns (actual_mints, allowed_max).
    allowed_max is a generous ceiling: how many mints the TTL math would
    require over the window, times `margin` (default 3x), plus 1 safety mint,
    plus one mint per token the vendor deliberately granted SHORT (see below).
    """
    mints = [t for t in token_log if route is None or t.get("route") == route]
    actual = len(mints)
    if ttl_s <= 0:
        return actual, actual  # can't reason about TTL; never flag
    minimal_needed = max(1, -(-window_s // ttl_s))  # ceil division
    allowed_max = int(minimal_needed * margin) + 1
    # The ceiling above paces the session by the vendor's NOMINAL TTL. A vendor
    # that deliberately hands out a shorter-lived token (the
    # FAULT_TOKEN_EXPIRY_MIDRUN / FAULT_TOKEN_EARLY_TTL_S family) forces one
    # extra legitimate mint per short grant: the connector honoured the
    # `expires_in` it was actually given, so nominal-TTL pacing understates what
    # a CORRECT connector must do.
    #
    # Measured 2026-08-07 on task-0049/writeback_under_pressure: the vendor
    # granted one 5s token against a 60s nominal TTL, so gold minted 5 against a
    # ceiling of 4 — unsatisfiable for any correct connector, the same failure
    # shape as the app_runs case above. Counting short grants (rather than
    # re-pacing the whole window off the shortest TTL) keeps the ceiling tight:
    # a connector that re-mints on every request is handed nominal-TTL tokens,
    # so its ceiling does not move and it is still caught.
    short_grants = sum(
        1
        for t in mints
        if isinstance(t.get("expires_in"), (int, float)) and 0 < float(t["expires_in"]) < ttl_s
    )
    return actual, allowed_max + short_grants


def retry_after_violations(request_log: list[Entry]) -> list[Entry]:
    """Soft rule 2: a retry arrived before the advertised Retry-After elapsed.
    Looks for a 429 with retry_after, then the next request to the same
    (method, path) whose ts is sooner than retry_after seconds later."""
    violations = []
    sorted_log = sorted(request_log, key=lambda e: e.get("ts", 0))
    for i, entry in enumerate(sorted_log):
        if not entry.get("rate_limited"):
            continue
        retry_after = entry.get("retry_after")
        if retry_after is None:
            continue
        method, path = entry.get("method"), entry.get("path")
        for later in sorted_log[i + 1 :]:
            if later.get("method") == method and later.get("path") == path:
                gap = later.get("ts", 0) - entry.get("ts", 0)
                if gap < retry_after:
                    violations.append(later)
                break
    return violations


def request_budget_violations(
    request_log: list[Entry], *, limit: int, window_s: float
) -> list[Entry]:
    """Requests that exceed ``limit`` in any rolling ``window_s`` window.

    This is deliberately stricter than a vendor's fixed-window counter: if a
    connector stays inside every rolling window, it cannot exceed any aligned
    fixed window either.  Entries without a numeric timestamp are returned as
    violations because they cannot prove proactive pacing.
    """
    if limit <= 0 or window_s <= 0:
        return list(request_log)

    timed: list[tuple[float, Entry]] = []
    invalid: list[Entry] = []
    for entry in request_log:
        try:
            timed.append((float(entry["ts"]), entry))
        except (KeyError, TypeError, ValueError):
            invalid.append(entry)
    timed.sort(key=lambda item: item[0])

    violations = list(invalid)
    start = 0
    for end, (timestamp, entry) in enumerate(timed):
        while start <= end and timestamp - timed[start][0] >= window_s:
            start += 1
        if end - start + 1 > limit:
            violations.append(entry)
    return violations


def full_resync_violations(
    request_log: list[Entry],
    list_path: str,
    incremental_params: tuple[str, ...] = ("modified_since", "cursor"),
) -> list[Entry]:
    """Soft rule 3: once a request to list_path has used an incremental param,
    a later request to the same path with none of those params present is a
    full re-sync when incremental was already known to work."""
    sorted_log = sorted(
        (e for e in request_log if e.get("path") == list_path),
        key=lambda e: e.get("ts", 0),
    )
    seen_incremental = False
    violations = []
    for entry in sorted_log:
        query = entry.get("query") or {}
        used_incremental = any(p in query for p in incremental_params)
        if used_incremental:
            seen_incremental = True
            continue
        if seen_incremental:
            violations.append(entry)
    return violations


def resume_from_page1_violations(
    request_log: list[Entry],
    list_path: str,
    cursor_param: str = "cursor",
    offset_param: str = "offset",
) -> list[Entry]:
    """Soft rule 4: after a mid-pagination failure (5xx), the next request to
    list_path should resume from the last-seen cursor/offset watermark, not
    restart from the initial page (no cursor / offset=0)."""
    sorted_log = sorted(
        (e for e in request_log if e.get("path") == list_path),
        key=lambda e: e.get("ts", 0),
    )
    violations = []
    last_watermark: str | int | None = None
    pending_failure = False
    for entry in sorted_log:
        query = entry.get("query") or {}
        watermark = query.get(cursor_param, query.get(offset_param))
        status = int(entry.get("status", 200))
        if pending_failure:
            pending_failure = False
            is_restart = watermark in (None, 0, "0") and last_watermark not in (None, 0, "0")
            if is_restart:
                violations.append(entry)
        if status >= 500:
            pending_failure = True
        else:
            last_watermark = watermark
    return violations


DEFAULT_HOT_LOOP_WINDOW_S = 2.0


def hot_loop_violations(
    request_log: list[Entry],
    k: int = 5,
    window_s: float = DEFAULT_HOT_LOOP_WINDOW_S,
) -> list[Entry]:
    """Soft rule 5: > k *immediate* identical retries following a 401/5xx with no
    change in the request (same method, path, query).

    "Immediate" is the operative word, and until 2026-08-08 this function did not
    implement it: it grouped consecutive identical fingerprints and never looked
    at `ts`. Six retries 60s apart — a connector correctly honouring an
    advertised Retry-After — scored exactly the same as six retries 100ms apart.
    The check therefore failed CORRECT behaviour, which is the one thing
    conduct-rules.md's invariants-only litmus test forbids.

    Measured cost of the omission: task-0022 wanted a 15-req/60s rate budget so
    its limiter would fire regardless of machine load, but at 15 the repeated 429
    retries on the faulted offset (spaced by the honoured Retry-After of 8s)
    extended the identical-request run past k and gold FAILED
    `no_hot_loop_on_error`. The budget was loosened to 25 to work around this
    function rather than fixing it — a task tuned around a broken check.

    ``window_s`` is the gap above which two identical consecutive requests are no
    longer the same burst. Waiting longer than this between attempts IS backoff,
    so the run ends there and cannot become a violation. 2.0s is deliberately
    generous: the pathology worth flagging is a spin loop firing every few tens
    of milliseconds, and every real backoff schedule (including a bare 1s sleep,
    which the unit fixture models) clears the bar within a couple of attempts.
    Pass ``window_s=0`` to restore the old time-blind behaviour.
    """
    sorted_log = sorted(request_log, key=lambda e: e.get("ts", 0))
    violations: list[Entry] = []
    run: list[Entry] = []

    def fingerprint(e: Entry) -> tuple:
        return (e.get("method"), e.get("path"), tuple(sorted((e.get("query") or {}).items())))

    def gap_s(prev: Entry, cur: Entry) -> float:
        """Seconds between two entries; 0.0 when either ts is missing or junk.

        Defaulting to 0.0 keeps a log with no usable timestamps behaving exactly
        as it did before this parameter existed, rather than silently declaring
        every burst innocent."""
        try:
            return float(cur.get("ts", 0) or 0) - float(prev.get("ts", 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    triggered = False
    for entry in sorted_log:
        status = int(entry.get("status", 200))
        same_request = bool(run) and fingerprint(run[-1]) == fingerprint(entry)
        # A long pause breaks the burst even when the request is identical.
        continues_burst = same_request and (window_s <= 0 or gap_s(run[-1], entry) < window_s)
        if continues_burst:
            run.append(entry)
        else:
            if triggered and len(run) > k:
                violations.extend(run[k:])
            run = [entry]
            triggered = False
        if status == 401 or status >= 500:
            triggered = True
    if triggered and len(run) > k:
        violations.extend(run[k:])
    return violations


def header_value(entry: Entry, name: str) -> Any:
    """Case-insensitive lookup in a logged request's captured `headers` dict.
    Vendors log headers via `dict(request.headers)`, which Starlette lowercases,
    but declarations spell them conventionally (`If-Match`) — so compare folded."""
    headers = entry.get("headers")
    if not isinstance(headers, dict):
        return None
    target = name.lower()
    for key, value in headers.items():
        if str(key).lower() == target:
            return value
    return None


def _idempotency_token(entry: Entry, body_param: str | None) -> Any:
    """The idempotency token this entry carries, honouring the vendor's declared
    STYLE. Header-style vendors surface it as the log's `idempotency_key`;
    body-param vendors (e.g. paygrade's `request_id`) carry it inside the body
    and leave `idempotency_key` null, which made a CORRECT retry look blind
    (measured 2026-08-01 on task-0043: it cost gold its own check)."""
    token = entry.get("idempotency_key")
    if token:
        return token
    if body_param:
        body = entry.get("body")
        if isinstance(body, dict):
            return body.get(body_param)
    return None


def retried_write_groups(
    request_log: list[Entry],
    write_methods: tuple[str, ...] = ("POST", "PATCH", "PUT"),
    exclude_paths: tuple[str, ...] = (),
    *,
    body_param: str | None = None,
) -> list[list[Entry]]:
    """Groups of write requests with identical (method, path, body) that were
    sent more than once — the evidence slice for soft rule 6: without at least
    one actual retry, retry-idempotency conduct is unobservable.

    ``body_param`` excludes the vendor's body-carried idempotency field from the
    grouping key. Without it, a body-param contract is UNENFORCEABLE: a connector
    that mints a fresh token per attempt changes the body, so its retries land in
    different groups, the evidence slice comes up empty, and the exact pathology
    the contract exists to forbid is the one thing that escapes detection
    (measured 2026-08-01 while restoring task-0031/task-0043's declarations).
    Header-style vendors pass ``None`` and are grouped exactly as before.
    """
    excluded = set(exclude_paths)
    groups: dict[tuple, list[Entry]] = {}
    for entry in request_log:
        if entry.get("method") not in write_methods:
            continue
        if entry.get("path") in excluded:
            continue
        body = entry.get("body")
        if body_param and isinstance(body, dict) and body_param in body:
            body = {k: v for k, v in body.items() if k != body_param}
        key = (entry.get("method"), entry.get("path"), _stable_repr(body))
        groups.setdefault(key, []).append(entry)
    return [entries for entries in groups.values() if len(entries) >= 2]


def blind_retry_without_idempotency(
    request_log: list[Entry],
    write_methods: tuple[str, ...] = ("POST", "PATCH", "PUT"),
    exclude_paths: tuple[str, ...] = (),
    body_param: str | None = None,
    version_header: str | None = None,
) -> list[Entry]:
    """Soft rule 6: a non-idempotent write retried (same method+path+body)
    without an idempotency key on the repeat(s).

    ``exclude_paths`` drops endpoints that are not writeback writes even though
    they use a write method — notably OAuth token/authorize/session-exchange mint
    endpoints. Re-minting a token on a later sync pass is a legitimate, fully
    correct behavior, so counting the second POST as a "blind retry" would fail the
    conduct-rules.md invariants-only litmus test (a correct connector trips it).

    With ``body_param`` declared, carrying *a* token is not enough — the retry must
    carry the SAME token as the first attempt. Re-rolling the token per attempt is
    precisely what a body-param idempotency contract forbids (it is what makes the
    vendor treat the retry as a fresh write), so a differing token is a violation,
    not compliance. Header-style vendors (``body_param=None``) keep the original
    presence-only rule so their measured baselines are unchanged.

    ``version_header`` names an optimistic-concurrency precondition header (e.g.
    onboardly's ``If-Match``). A resubmission that carries a DIFFERENT version than
    the attempt before it is an *informed* retry: the connector was told its version
    was stale (409), re-read, and resubmitted against the current one. That is the
    correct behaviour, and without this exemption it read as a blind retry — the
    check was unsatisfiable for a correct connector on such an endpoint (measured
    2026-08-01 on task-0031: gold failed it in both push scenarios). A retry
    carrying the SAME stale version is still a violation: nothing was learned
    between attempts, which is exactly the pathology worth flagging."""
    violations = []
    for entries in retried_write_groups(
        request_log, write_methods, exclude_paths, body_param=body_param
    ):
        entries = sorted(entries, key=lambda e: e.get("ts", 0))
        first_token = _idempotency_token(entries[0], body_param)
        for index, retry in enumerate(entries[1:], start=1):
            if version_header:
                previous = header_value(entries[index - 1], version_header)
                current = header_value(retry, version_header)
                if current is not None and current != previous:
                    continue
            token = _idempotency_token(retry, body_param)
            if not token:
                violations.append(retry)
            elif body_param and token != first_token:
                violations.append(retry)
    return violations


def _stable_repr(body: Any) -> str:
    import json

    return json.dumps(body, sort_keys=True, default=str)
