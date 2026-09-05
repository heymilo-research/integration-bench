"""oauth_hardening_under_faults -- once migrated, the new OAuth CC path must
survive the two faults bullpen exercises on the data plane: token death mid-
crawl (403, not 401 -- re-auth and retry) and the always-on body-only 429
rate limit (no Retry-After header on this vendor).

Continues from wherever legacy_backfill_then_sunset.py left the stack
(already migrated to OAuth, state.json watermarks at the checkpoint-1 world).
Lowers the rate limit and forces the token-expiry fault, then recreates at
CHECKPOINT=5 (a fresh boot -- resets the fault budget and the request-count
threshold) so the run is guaranteed to cross both faults deterministically:

  - FAULT_TOKEN_EXPIRY_MIDRUN=1, BP_FAULT_EXPIRY_AFTER_N=2: the first grant's
    token dies after its 2nd successful use.
  - BP_RATE_LIMIT=2 / BP_RATE_WINDOW=2 / BP_RATE_RETRY_AFTER_S=2: a tiny
    window so a handful of incremental GETs trips it, and the advertised
    backoff is short enough for a fast grading run.

L1 : app exits 0 (plumbing).
L3 : reauth_on_403 -- a 403 ("session invalid") is observed on a data
     endpoint and the SAME logical request later succeeds under a fresh
     token (a 2nd token-log mint after the 403) -- documented vendor
     behavior (docs/auth.md's "known quirk"), so required but not the trap.
     retry_after_body_honored -- the request that follows a 429 waits at
     least the body's `retry_after_s` before repeating the SAME (method,
     path) -- the trap: docs/index.md's rate-limit table claims a
     Retry-After HEADER exists; this vendor never sends one, only the body
     field, so a connector faithfully reading the header never backs off.
L2  : builtin conduct gates/soft checks.

Grading note (2026-08-07): the three whole-file ``*_matches_fixture`` compares
against the checkpoint-5 fixtures were deleted in an earlier migration pass and
**nothing replaced them**, so this scenario stopped grading its own output
entirely — the only surviving use of the store was an ``ok_files`` flag ANDed into
retry_after_body_honored. Restored as per-entity row counts plus per-field
equality against ``*_final.json`` (+1 each; measured discriminators — the starter
never gets this far).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2
from bench.verifier.io import read_json_output

from _scenario_util import diff_detail, row_count_ok, row_diff  # noqa: E402

_ENTITIES = ("candidates", "jobs", "applications")


def _fixture(ctx, entity: str) -> list:
    try:
        return json.loads(
            (ctx.fixtures / f"{entity}_final.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return []


async def run(ctx) -> None:
    vendor = ctx.vendor("bullpen")

    # State its OWN config in full rather than relying on whatever
    # legacy_backfill_then_sunset.py left in the shared vendor_env dict --
    # that override persists for the whole verdict (not per-scenario), so an
    # omitted key here would silently inherit the previous scenario's value.
    # This scenario continues the post-sunset story (already migrated to
    # OAuth), so legacy auth stays explicitly disabled -- restated, not
    # inherited.
    vendor._stack.vendor_env["BP_LEGACY_AUTH_ENABLED"] = "0"
    vendor._stack.vendor_env["FAULT_TOKEN_EXPIRY_MIDRUN"] = "1"
    vendor._stack.vendor_env["BP_FAULT_EXPIRY_AFTER_N"] = "2"
    vendor._stack.vendor_env["BP_RATE_LIMIT"] = "2"
    vendor._stack.vendor_env["BP_RATE_WINDOW"] = "2"
    vendor._stack.vendor_env["BP_RATE_RETRY_AFTER_S"] = "2"
    vendor.recreate(checkpoint=5)

    marker_ts = vendor.request_log()
    marker_ts = max((e.get("ts", 0) for e in marker_ts), default=-1.0)

    exit_code, _stdout, stderr = ctx.app.run()
    # AND-ed with this run's own data-plane traffic (task-0043 pattern,
    # 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing run, and
    # output-readability is no evidence here — leftover files from
    # legacy_backfill_then_sunset read fine (the INERTIA problem noted below).
    ran_data_calls = [
        e for e in vendor.request_log()
        if e.get("ts", 0) > marker_ts and str(e.get("path", "")).startswith("/v2/")
    ]
    ctx.check(
        "app_exit_ok",
        exit_code == 0 and len(ran_data_calls) > 0,
        f"exit={exit_code} data_plane_calls={len(ran_data_calls)} stderr={stderr[:500]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    ok_files = True
    for name in _ENTITIES:
        out_path = ctx.output_dir / f"{name}.json"
        out = read_json_output(out_path, timeout_s=15.0 if exit_code == 0 else 0.5)
        if out is None:
            ctx.check(
                f"{name}_output_exists",
                False,
                f"missing/unreadable {out_path.name}",
                pass_value=0,
                fail_value=-1,
                mandatory=False,
            )
            ok_files = False
            continue
        if exit_code != 0:
            # Same INERTIA concern the removed whole-file compare used to
            # guard against (task-0044, 2026-08-01): a crashed run's stale
            # leftover file from legacy_backfill_then_sunset is not this
            # run's work.
            ok_files = False

        # Restores the deleted whole-file compare, per entity and per field. The
        # `exit_code == 0` conjunct carries the same INERTIA guard: a leftover
        # file from the previous scenario reads fine and is not this run's work.
        want = _fixture(ctx, name)
        count_ok, count_detail = row_count_ok(out, want)
        ctx.check(
            f"cp5_row_count:{name}",
            exit_code == 0 and count_ok,
            f"{name}: {count_detail}",
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )
        diffs = row_diff(out, want)
        ctx.check(
            f"cp5_fields_exact:{name}",
            exit_code == 0 and not diffs,
            f"{name}: {diff_detail(diffs)}",
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )

    # ------------------------------------------------------------------- L3
    log = [e for e in vendor.request_log() if e.get("ts", 0) > marker_ts]
    log.sort(key=lambda e: e.get("ts", 0))
    token_log = [e for e in vendor.token_log() if e.get("ts", 0) > marker_ts]

    data_calls = [e for e in log if str(e.get("path", "")).startswith("/v2/")]
    fault_403 = next((e for e in data_calls if e.get("status") == 403), None)
    recovered_after_403 = False
    if fault_403 is not None:
        recovered_after_403 = any(
            e.get("ts", 0) > fault_403.get("ts", 0)
            and e.get("path") == fault_403.get("path")
            and e.get("status") == 200
            for e in data_calls
        )
    ctx.check(
        "reauth_on_403",
        fault_403 is not None and recovered_after_403 and len(token_log) >= 2,
        f"saw_403={fault_403 is not None} recovered={recovered_after_403} mints_this_run={len(token_log)}",
        # Documented vendor behavior (docs/auth.md's "known quirk"): required
        # but not the doc-lie trap.
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    rate_limited = [e for e in data_calls if e.get("rate_limited")]
    honored = True
    detail = []
    for entry in rate_limited:
        retry_after = entry.get("retry_after_s")
        if retry_after is None:
            continue
        later = next(
            (e for e in data_calls
             if e.get("ts", 0) > entry.get("ts", 0) and e.get("path") == entry.get("path")),
            None,
        )
        if later is None:
            honored = False
            detail.append(f"no follow-up call to {entry.get('path')}")
            continue
        gap = later.get("ts", 0) - entry.get("ts", 0)
        if gap < retry_after:
            honored = False
            detail.append(f"gap={gap:.2f}s < retry_after_s={retry_after} on {entry.get('path')}")
    ctx.check(
        "retry_after_body_honored",
        len(rate_limited) > 0 and honored and ok_files,
        f"rate_limited_calls={len(rate_limited)} honored={honored} " + "; ".join(detail[:3]),
        # The trap: docs/index.md's rate-limit table lies about a
        # Retry-After header; this vendor only ever carries retry_after_s in
        # the body, so a connector faithful to the docs never backs off.
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    await builtin_l2(ctx)
