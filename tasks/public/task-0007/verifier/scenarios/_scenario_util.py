"""Shared helpers for the task-0007 scenarios.

Scenarios share ONE postgres instance for the whole grade (only the vendor
container is recreated per scenario) — see ``reset_store`` for the isolation
primitive, and ``watermark_integrity_incremental`` for the one scenario that
deliberately does NOT reset.

HOW THE MID-CRAWL DEATH IS PRODUCED (rewritten 2026-08-01)
----------------------------------------------------------
The previous version started the connector as a detached ``compose run -d``
container, watched the vendor's request log through a ``docker compose exec -T
vendor tail -F`` stream, and ``docker kill``ed the container the moment N
subjects pages had been served. That mechanism was measurably broken in two
independent ways:

1. ``tail -F -n +1`` attaches to the log file that is ALREADY on the
   ``vendor-logs`` volume. The vendor truncates its logs in its FastAPI
   lifespan, but ``compose up --force-recreate`` returns before that runs, so
   the watcher replayed the PREVIOUS scenario's lines and reached its target
   count instantly. Measured: ``pages_observed=6`` in ``forced_restart_resume``
   (exactly crawl_start's 6 subjects pages) and ``14`` in
   ``budget_pressure_recovery`` (exactly forced_restart_resume's 8 probes + 6
   resume pages). The kill therefore fired before the connector had issued a
   single request — 0 rows in the store at "kill time", and NO persisted
   cursor, so the cursor-expiry mechanic never ran at all and gold scored
   identically to the do-nothing starter (0.931 both).
2. Even with the replay fixed it could not work: the connector's entire
   21-request crawl completes in ~60 ms on the local docker network (measured),
   while a ``docker kill`` round trip is hundreds of ms. A wall-clock race
   against the app is unwinnable and is forbidden by the harness conventions
   anyway.

Both are replaced by the vendor's own deterministic, seed-independent fault
knob: ``FAULT_5XX_ON_PAGE="/v1/subjects:4:500:1"`` makes page 4 of
``/v1/subjects`` answer 500 exactly once. The connector's transport does not
swallow 5xx, so the process dies mid-crawl — which is precisely the
scheduler-killed-us-mid-run event the ticket is about — leaving pages 1-3
committed and the pagination cursor persisted. No timing, no races, no
detached containers: the interruption point is a property of the vendor
configuration, identical on every run and every host.

The cursor is then aged past ``VT_CURSOR_TTL_REQS=5`` by verifier-issued list
requests while the connector is not running (the TTL is REQUEST-indexed and
per-collection, and counts any client's traffic), and the next ``sync``
invocation is the restart. Its very first list request replays the persisted
cursor and gets the documented ``410 cursor_expired``.

Verifier-injected probe traffic hygiene
---------------------------------------
Each probe carries its own distinct, wide-open ``modified_since`` (1, 2, 3 …
— every real ``updated_at`` is ~1.77e9, so these match everything). That is
deliberate on three counts: identical back-to-back requests would look like a
retry hot loop to ``builtin_l2.no_hot_loop_on_error`` (which has no exclusion
mechanism and DID fail gold on the verifier's own probes, measured), a filterless
list would look like an unnecessary full resync once the connector had already
narrowed, and a distinct filter per probe keeps every fingerprint unique. The
probes still bump the vendor's per-collection request counter, which is all the
cursor TTL cares about.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from bench.verifier.io import read_json_output

ENTITIES = ("subjects", "checks", "reports")
VENDOR = "vettly"                 # task.yaml vendors-block key
VENDOR_SERVICE = "vendor"         # literal compose service name

# Every vendor env knob this task ever touches, with its OFF/default value.
# Every recreate writes the FULL dict, so a fault one scenario enables can
# never leak into the next (the compose stack's vendor_env dict is shared for
# the whole verdict).
VENDOR_ENV_DEFAULTS = {
    "CHECKPOINT": "0",
    "VT_CURSOR_TTL_REQS": "5",
    "FAULT_5XX_ON_PAGE": "",
    "FAULT_TOKEN_EXPIRY_MIDRUN": "0",
    "VT_TOKEN_RL_LIMIT": "3",
}

# Page 4 of subjects/checks and page 3 of reports: the fault point for each
# collection. Chosen so the pre-death partial state is a large, proper,
# non-empty subset (150/300 subjects, 150/400 checks, 100/250 reports).
FAULT_PAGE = {"subjects": 4, "checks": 4, "reports": 3}


def _stack(ctx):
    return ctx.app._stack


# ---------------------------------------------------------------------------
# Vendor lifecycle
# ---------------------------------------------------------------------------

def recreate_vendor(ctx, *, checkpoint: int, faults: str = "", token_expiry: bool = False,
                    token_rl_limit: int = 3) -> None:
    """Recreate the vendor with a FULLY specified env, then block until it
    answers HTTP.

    The readiness wait is load-bearing, not politeness: the vendor deletes
    ``requests.jsonl``/``tokens.jsonl`` in its lifespan startup, before it
    serves anything, so a successful response is proof that this scenario's
    request log is fresh. Reading the log (or streaming it) before that point
    yields the PREVIOUS scenario's traffic — the exact defect that broke this
    task's star mechanic.
    """
    env = dict(VENDOR_ENV_DEFAULTS)
    env["CHECKPOINT"] = str(checkpoint)
    env["FAULT_5XX_ON_PAGE"] = faults
    env["FAULT_TOKEN_EXPIRY_MIDRUN"] = "1" if token_expiry else "0"
    env["VT_TOKEN_RL_LIMIT"] = str(token_rl_limit)

    stack = _stack(ctx)
    # MERGE over the existing env rather than replacing it: the stack seeds
    # credentials and the real checkpoint_env name at construction, and a bare
    # assignment drops them. Fault isolation is still total because
    # VENDOR_ENV_DEFAULTS above restates every fault knob at its OFF value on
    # every call, so nothing a previous scenario armed can survive.
    #
    # Assign through the `vendor_env` property, NOT `vendor_envs["vendor"]`.
    # The compose-unit lane keys vendor_envs by BLOCK name ("vettly"); writing
    # the literal compose service name inserted a key no other code resolves,
    # so `_write_vendor_cfg` raised KeyError('vendor') and the whole grade
    # crashed to zero checks — while `recreate()` looked up the block key,
    # found nothing, and armed no faults at all. The property setter resolves
    # to the right key on every stack.
    stack.vendor_env = {**stack.vendor_env, **env}
    ctx.vendor(VENDOR).recreate(checkpoint=checkpoint)

    from bench.health import wait_for_http

    base = ctx.vendor(VENDOR).base_url
    assert base, "vendor data port not published"
    wait_for_http(f"{base}/", timeout_s=90.0)


def fault_spec(*entities: str) -> str:
    """``FAULT_5XX_ON_PAGE`` value that kills the crawl once per named
    collection."""
    return ",".join(f"/v1/{e}:{FAULT_PAGE[e]}:500:1" for e in entities)


# ---------------------------------------------------------------------------
# Store isolation / inspection
# ---------------------------------------------------------------------------

def reset_store(ctx) -> None:
    """Drop the canonical sqlite DB so each scenario starts empty.

    Scenarios share one DB file on the ``canonical-data`` volume for the whole
    grade; without this, tombstones/watermarks from an earlier scenario leak.
    """
    from bench.canonical_sqlite import reset_canonical_on_stack

    reset_canonical_on_stack(_stack(ctx))



def dump_store(ctx) -> dict[str, list[dict[str, Any]]] | None:
    """Snapshot the canonical store to output/{entity}.json and read it back.
    Returns None if the dump failed or any file never became readable."""
    for entity in ENTITIES:
        try:
            (ctx.output_dir / f"{entity}.json").unlink()
        except OSError:
            pass
    code, _out, _err = ctx.app.run(["dump"])
    if code != 0:
        return None
    result: dict[str, list[dict[str, Any]]] = {}
    for entity in ENTITIES:
        rows = read_json_output(ctx.output_dir / f"{entity}.json", timeout_s=15.0)
        if rows is None:
            return None
        result[entity] = rows
    return result


def load_fixture(ctx, name: str) -> list[dict[str, Any]]:
    return json.loads((ctx.fixtures / name).read_text(encoding="utf-8"))


def fixtures_for(ctx, checkpoint: int) -> dict[str, list[dict[str, Any]]]:
    return {e: load_fixture(ctx, f"{e}_checkpoint_{checkpoint}.json") for e in ENTITIES}


def missing_source_ids(rows: list[dict[str, Any]], fixture: list[dict[str, Any]]) -> list[str]:
    have = {r.get("source_id") for r in rows}
    return [r["source_id"] for r in fixture if r["source_id"] not in have]


def rows_are_correct_subset(rows: list[dict[str, Any]], fixture: list[dict[str, Any]]) -> tuple[bool, str]:
    """Every row present must exactly equal its fixture counterpart. Admits any
    subset — used for state observed mid-crawl, where only correctness (never a
    count) is a fixture-pinnable property."""
    fixture_by_id = {r["source_id"]: r for r in fixture}
    bad = [
        row.get("source_id") for row in rows
        if fixture_by_id.get(row.get("source_id")) != row
    ]
    if bad:
        return False, f"{len(bad)} row(s) mismatch fixture: {bad[:5]}"
    return True, f"{len(rows)} row(s), all byte-correct"


def no_duplicate_source_ids(rows: list[dict[str, Any]]) -> bool:
    ids = [r["source_id"] for r in rows]
    return len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Verifier-side probe traffic (ages the persisted cursor while the app is down)
# ---------------------------------------------------------------------------

def _mint_probe_token(base_url: str, client_id: str, client_secret: str) -> str:
    form = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/oauth/token", data=form, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))["access_token"]


class CursorAger:
    """Issues list requests directly against the vendor to age its
    per-collection request counter, and records exactly which log indices its
    own traffic occupies so connector-conduct checks can exclude it."""

    def __init__(self, ctx) -> None:
        self._ctx = ctx
        self._vendor = ctx.vendor(VENDOR)
        self._token: str | None = None
        self.request_indices: set[int] = set()
        self.token_indices: set[int] = set()

    def _token_header(self) -> dict[str, str]:
        if self._token is None:
            tok_before = len(self._vendor.token_log())
            req_before = len(self._vendor.request_log())
            self._token = _mint_probe_token(
                self._vendor.base_url,
                self._ctx.secrets["VT_CLIENT_ID"],
                self._ctx.secrets["VT_CLIENT_SECRET"],
            )
            # The mint itself lands in BOTH logs; exclude it from both slices.
            self.token_indices.update(range(tok_before, len(self._vendor.token_log())))
            self.request_indices.update(range(req_before, len(self._vendor.request_log())))
        return {"Authorization": f"Bearer {self._token}"}

    def age(self, entity: str, n_probes: int = 8) -> None:
        """``VT_CURSOR_TTL_REQS=5`` needs 5 further list requests before the
        persisted cursor's own replay is the 6th and dies; 8 is comfortably
        past that for any fault page. One throwaway grant is minted lazily and
        reused for every collection, so the connector's own token budget is
        never the thing the probes consume."""
        headers = self._token_header()
        base = self._vendor.base_url
        req_before = len(self._vendor.request_log())
        for i in range(n_probes):
            # Distinct wide-open filter per probe: see the module docstring.
            url = f"{base}/v1/{entity}?modified_since={i + 1}"
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(url, method="GET", headers=headers), timeout=15
                ) as resp:
                    resp.read()
            except urllib.error.HTTPError:
                pass
        self.request_indices.update(range(req_before, len(self._vendor.request_log())))

    def connector_requests(self) -> list[tuple[int, dict[str, Any]]]:
        return [
            (i, e) for i, e in enumerate(self._vendor.request_log())
            if i not in self.request_indices
        ]

    def connector_tokens(self) -> list[dict[str, Any]]:
        return [
            t for i, t in enumerate(self._vendor.token_log())
            if i not in self.token_indices
        ]


# ---------------------------------------------------------------------------
# Orchestration: crash -> age -> restart, once per named collection
# ---------------------------------------------------------------------------

def interrupted_recovery_drive(ctx, entities: tuple[str, ...]) -> dict[str, Any]:
    """Drive ``sync`` repeatedly against a stack faulted once per collection in
    ``entities``.

    Each invocation dies on the first collection whose fault has not fired yet;
    the persisted cursor for that collection is then aged past its TTL before
    the next invocation, so the restart's first list request is guaranteed to
    replay a dead cursor. Exactly ``len(entities) + 1`` invocations: one death
    per collection plus the final clean pass.

    Returns the per-round exit codes, the partial store observed after the
    FIRST death, and the ager (for conduct-slice exclusion).
    """
    ager = CursorAger(ctx)
    exits: list[int] = []
    first_partial: dict[str, list[dict[str, Any]]] | None = None
    aged: list[str] = []

    for round_no in range(len(entities) + 1):
        code, _out, err = ctx.app.run(["sync"])
        exits.append(code)
        if round_no == 0:
            first_partial = dump_store(ctx)
        if round_no == len(entities):
            return {
                "exits": exits,
                "first_partial": first_partial,
                "aged": aged,
                "ager": ager,
                "last_stderr": err or "",
            }
        # A death leaves exactly one collection holding a cursor: age it.
        for entity in _entities_with_cursor(ctx):
            ager.age(entity)
            aged.append(entity)
    raise AssertionError("unreachable")


def _entities_with_cursor(ctx) -> list[str]:
    """Which collections currently hold a persisted pagination cursor.

    Read from the canonical store's own ``sync_state`` table rather than
    guessed from traffic: a submission is free to name its state keys
    ``<entity>.cursor`` or anything else, so fall back to "every collection"
    when the shape is unrecognised — aging a collection with no live cursor is
    harmless (it only bumps a counter)."""
    import sqlite3
    from pathlib import Path

    stack = _stack(ctx)
    db_path = getattr(stack, "_canonical_host_db", None)
    if db_path is None:
        return list(ENTITIES)
    if not Path(db_path).is_file():
        return list(ENTITIES)
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            cur = conn.execute(
                "SELECT key FROM canonical_sync_state WHERE value IS NOT NULL"
            )
            keys = [str(r[0]).strip() for r in cur.fetchall() if r and r[0]]
        finally:
            conn.close()
    except sqlite3.Error:
        return list(ENTITIES)
    found = [
        e
        for e in ENTITIES
        if any(k.startswith(f"{e}.") and k.endswith(".cursor") for k in keys)
    ]
    return found or list(ENTITIES)


# ---------------------------------------------------------------------------
# Request-log analysis (all structural — never a cross-process timestamp
# comparison, never a wall-clock threshold)
# ---------------------------------------------------------------------------

def _filter_of(entry: dict[str, Any]) -> str | None:
    return (entry.get("query") or {}).get("modified_since")


def pass_filter_preserved(connector_requests: list[tuple[int, dict[str, Any]]], entity: str) -> tuple[bool, str]:
    """After a ``410 cursor_expired`` on ``/v1/<entity>``, every later list
    request for that collection must carry the SAME ``modified_since`` the
    expired pass was using — the filter the 410'd request itself carried.

    This is the ticket's rule stated structurally: recovery may drop the
    cursor, but it may not re-anchor the pass anywhere else. Narrowing it (the
    starter re-anchors at the newest ``updated_at`` seen so far) silently skips
    every unreached row with an older timestamp; widening it to nothing is the
    slow full re-crawl the ticket also rules out. A connector that never let a
    cursor expire has nothing to mishandle and passes vacuously — the
    final-state checks are what hold it to completeness.

    Scope: only the RECOVERED pass, never later passes. A cursor-less list
    request is by definition the start of a fresh pass, so the recovered pass
    is the run of requests from the 410 up to (excluding) the next cursor-less
    request after the recovery's own re-anchoring one. Without that bound this
    would also grade the LATER, legitimately-different anchor of the next
    incremental pass, which the same scenario's remaining rounds do issue.
    """
    path = f"/v1/{entity}"
    entries = [e for _i, e in connector_requests if e.get("path") == path]
    expired_at = next(
        (idx for idx, e in enumerate(entries) if int(e.get("status", 200)) == 410), None
    )
    if expired_at is None:
        return True, "no cursor expiry observed for this collection"
    want = _filter_of(entries[expired_at])

    recovered_pass: list[dict[str, Any]] = []
    for offset, entry in enumerate(entries[expired_at + 1:]):
        cursorless = not (entry.get("query") or {}).get("cursor")
        if offset > 0 and cursorless:
            break  # a fresh pass begins here
        recovered_pass.append(entry)

    offenders = [(e.get("query") or {}) for e in recovered_pass if _filter_of(e) != want]
    if offenders:
        return False, (
            f"pass filter was modified_since={want!r} when the cursor expired; "
            f"{len(offenders)} of {len(recovered_pass)} request(s) in the recovered "
            f"/v1/{entity} pass used a different anchor, first={offenders[0]}"
        )
    return True, (
        f"all {len(recovered_pass)} request(s) in the recovered pass kept "
        f"modified_since={want!r}"
    )


def cursor_expiry_observed(connector_requests: list[tuple[int, dict[str, Any]]]) -> tuple[bool, str]:
    expired = [e.get("path") for _i, e in connector_requests if int(e.get("status", 200)) == 410]
    return bool(expired), f"410 cursor_expired responses served to the connector: {expired}"


def forced_401_count(connector_requests: list[tuple[int, dict[str, Any]]]) -> int:
    return sum(
        1 for _i, e in connector_requests
        if str(e.get("path", "")).startswith("/v1/") and int(e.get("status", 0)) == 401
    )


def list_request_count(connector_requests: list[tuple[int, dict[str, Any]]], path: str) -> int:
    return sum(1 for _i, e in connector_requests if e.get("path") == path and e.get("method") == "GET")
