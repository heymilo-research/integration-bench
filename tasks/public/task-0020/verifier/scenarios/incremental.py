"""Scenario 2 — incremental catch-up via a persisted watermark, hardened with
a tombstone/ordering leg under retry pressure (task-0020 spec §3b).

Flow:
  1. Recreate the vendor at checkpoint 0 and back-fill (``sync``). This sets the
     watermark = max candidate ``modified_at`` (UTC epoch seconds) seen so far.
  2. Advance the tenant's timeline: recreate the vendor at checkpoint 1, which
     applies the scripted mutation timeline. The three CANDIDATE mutations are:
     an update (cand_00042 pipeline_stage -> offer), a delete (cand_00017 ->
     tombstone, row retained), and a create (cand_09000) — the SAME
     checkpoint-1 mutation set as always (spec §3b: no new fixture/vendor
     data). Recreating the vendor also resets its request log, so the log
     grades only what follows.

     Before this recreate, compose ``FAULT_5XX_ON_PAGE`` onto the SAME
     checkpoint-1 mutation set (an existing globalhire fault knob, already
     used by task-0022 — no new fault machinery). The incremental result set
     is only 3 rows, so it lands on ONE page at offset 0; targeting that
     offset guarantees the fault actually fires rather than sitting unused.
  3. Run ``sync`` again. The candidate page at offset 0 returns 500 twice,
     then serves the real page. A connector that gives up, restarts a full
     crawl, or lets the retry corrupt/race the tombstone against the update
     fails the checks below. This stays narrow — ordering + watermark
     integrity only — and deliberately does NOT duplicate task-0022's own
     `resume_not_restart` / `retry_after_honored` forensics ladder (spec §6
     divergence: 0022 is the dedicated "faults on globalhire" task).
  4. Run ``sync`` a THIRD time (same container, same checkpoint, fault budget
     already spent) — the watermark-regression probe: a connector that
     recomputed its watermark from a mid-retry, partial view of the faulted
     page could double back, either re-crawling the whole table or re-polling
     from an earlier-than-correct ``modified_since``.

This bites the same three lies as the backfill, plus the watermark round-trip
(as before) — now under fault pressure, plus the new ordering/watermark
checks, plus the split-brain (v2) regression guardrail (spec §3a): this task
stays pinned v1-only.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    assert_gh_v2_disabled,
    diff_detail,
    dump_store,
    load_fixture,
    reset_store,
    row_diff,
)

# Mutation-timeline TRUE-UTC epoch-second stamps (mutations.yaml `at` + the
# vendor's fixed mutation-base instant; see vendors/globalhire/src/globalhire/
# state.py `_mutation_instant` / `_MUT_BASE_OFFSET_S` = BASE_EPOCH_S +
# 10_000_000). Independent of each record's own per-record offset rendering —
# these are the instants each mutation lands at, in strict chronological
# order: update(100) < delete(200) < create(300).
_UPDATE_UTC_S = 1_777_571_300  # cand_00042 pipeline_stage -> offer
_DELETE_UTC_S = 1_777_571_400  # cand_00017 tombstoned
_CREATE_UTC_S = 1_777_571_500  # cand_09000 created

# The cp1 incremental result set (3 candidates) fits on one page; offset 0 is
# the only page requested, so faulting it guarantees the fault fires.
_FAULT_OFFSET = 0
_FAULT_SPEC = f"{_FAULT_OFFSET}:2"  # 2 hits at 500, then serve normally.


def _candidate_pages(log):
    return sorted(
        (e for e in log if e.get("method") == "GET" and e.get("path") == "/v1/candidates"),
        key=lambda e: e.get("ts", 0),
    )


def _offset(entry) -> int:
    try:
        return int((entry.get("query") or {}).get("offset") or 0)
    except (TypeError, ValueError):
        return -1


def _modified_since_utc_s(entry) -> int | None:
    """Parse an entry's `modified_since` query value to a UTC epoch second,
    the same way the vendor/gold client do (honor a numeric offset OR a `Z`
    suffix). Returns None if the entry carries no parseable value."""
    raw = (entry.get("query") or {}).get("modified_since")
    if not raw:
        return None
    v = str(raw).strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.astimezone(timezone.utc).timestamp())


async def run(ctx) -> None:
    handle = ctx.vendor("globalhire")

    # -- 1. cp0 backfill (establishes the watermark) ------------------------
    reset_store(ctx)
    handle.recreate(checkpoint=0)
    marker_ts = max((e.get("ts", 0) for e in handle.request_log()), default=-1.0)
    code, _out, err = ctx.app.run(["sync"])
    # AND-ed with this phase's own data-plane traffic (task-0043 pattern,
    # 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing run, and
    # nothing is dumped until after the cp1 pass below.
    backfill_calls = [
        e for e in handle.request_log()
        if e.get("ts", 0) > marker_ts and str(e.get("path", "")).startswith("/v1/")
    ]
    ctx.check("incr_backfill_exit_ok",
        code == 0 and len(backfill_calls) > 0,
        f"exit={code} data_plane_calls={len(backfill_calls)} stderr={err[:400]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # -- 2. compose the tombstone/ordering leg onto cp1 ----------------------
    handle._stack.vendor_env["FAULT_5XX_ON_PAGE"] = _FAULT_SPEC
    handle.recreate(checkpoint=1)

    # -- 3. incremental reconciliation poll, under fault pressure -----------
    code, _out, err = ctx.app.run(["sync"])

    store = dump_store(ctx)
    # AND-ed with store readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run.
    ctx.check("incr_poll_exit_ok",
        code == 0 and store is not None,
        f"exit={code} stderr={err[:400]} store_readable={store is not None}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    if store is None:
        ctx.check(
            "incr_store_readable",
            False,
            "dump produced no output",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return

    # Replaces the `store == cp1` blob compare (`incremental_matches_fixture`)
    # with a per-row, per-field diff (see initial_sync.py for why the blob
    # compare's detail was useless over 6001 rows).
    cp1 = load_fixture(ctx, "candidates_checkpoint_1.json")
    diffs = row_diff(store, cp1)
    ctx.check("incremental_rows_exact",
        not diffs,
        diff_detail("candidates@cp1", store, cp1, diffs),
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # cp1 = 6000 base candidates + the created cand_09000 = 6001 rows (the
    # delete is a flag flip, not a removal; the update is in place).
    ctx.check("incremental_row_count_6001",
        len(store) == 6001,
        f"store rows={len(store)} (expected 6001)",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    by_id = {r["source_id"]: r for r in store}

    # The update landed: cand_00042's pipeline_stage bumped to `offer`. This
    # also re-exercises stale_field_name (the field is pipeline_stage).
    c42 = by_id.get("cand_00042", {})
    ctx.check("incremental_applied_update",
        c42.get("data", {}).get("pipeline_stage") == "offer",
        f"cand_00042 pipeline_stage={c42.get('data', {}).get('pipeline_stage')}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # The delete landed as a tombstone (row retained, is_deleted true).
    c17 = by_id.get("cand_00017", {})
    ctx.check("incremental_applied_tombstone",
        c17.get("is_deleted") is True,
        f"cand_00017 is_deleted={c17.get('is_deleted')}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # The create landed (new candidate present exactly once).
    c9000 = by_id.get("cand_09000")
    ctx.check("incremental_applied_create",
        c9000 is not None,
        "cand_09000 was never inserted by the incremental poll",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    request_log = handle.request_log()

    # The reconciliation pass must be incremental: it used modified_since on the
    # candidate list endpoint (not a blind full re-crawl).
    used_modified_since = any(
        e.get("path") == "/v1/candidates" and "modified_since" in (e.get("query") or {})
        for e in request_log
    )
    # The watermark round-trip: a connector that never persisted a true-UTC
    # watermark cannot poll incrementally at all, so this is the trap for the
    # incremental leg. Recorded unconditionally.
    ctx.check("incremental_used_modified_since",
        used_modified_since,
        "no GET /v1/candidates carried modified_since",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # And it did NOT re-crawl the whole table on the incremental pass: the
    # filtered result set is tiny (a handful of records at/after the watermark),
    # so only a few candidate pages carry modified_since. Retries through the
    # fault legitimately repeat the SAME page (<=3 hits at offset 0 given the
    # 2-hit fault quota), so the ceiling is a little more generous than the
    # fault-free case; a full re-crawl would still be >= 60.
    incr_pages = [
        e for e in request_log
        if e.get("path") == "/v1/candidates" and e.get("method") == "GET"
        and "modified_since" in (e.get("query") or {})
    ]
    ctx.check("incremental_not_full_resync",
        1 <= len(incr_pages) <= 6,
        f"{len(incr_pages)} modified_since candidate page(s); a full re-crawl would be >= 60",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # ------------------------------------------------------------------
    # (b) Tombstone/ordering leg (spec §3b). The delete and the update must
    # resolve in the CORRECT relative order once the connector has retried
    # through the injected fault — checked via each row's OWN true-UTC
    # `updated_at`, not via mere presence, so a retry that races/corrupts
    # in-flight state cannot hide behind "the right rows just showed up."
    # ------------------------------------------------------------------
    pages = _candidate_pages(request_log)

    # Non-vacuity guard: the fault must have actually fired and been retried
    # to a 200 at the SAME offset — otherwise the checks below would pass by
    # never having been exercised.
    fault_fired = any(
        _offset(e) == _FAULT_OFFSET and int(e.get("status", 0)) >= 500 for e in pages
    )
    retried_ok = any(
        _offset(e) == _FAULT_OFFSET and int(e.get("status", 0)) == 200 for e in pages
    )
    ctx.check("candidate_page_retried_through_fault",
        fault_fired and retried_ok,
        f"fault_fired={fault_fired} retried_ok={retried_ok} "
        f"statuses={[int(e.get('status', 0)) for e in pages][:8]}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    update_stamp_ok = c42.get("updated_at") == _UPDATE_UTC_S
    delete_stamp_ok = c17.get("updated_at") == _DELETE_UTC_S
    create_stamp_ok = c9000 is not None and c9000.get("updated_at") == _CREATE_UTC_S
    ctx.check("tombstone_update_order_preserved_under_retry",
        update_stamp_ok and delete_stamp_ok and create_stamp_ok,
        f"cand_00042.updated_at={c42.get('updated_at')} (want {_UPDATE_UTC_S}) "
        f"cand_00017.updated_at={c17.get('updated_at')} (want {_DELETE_UTC_S}) "
        f"cand_09000.updated_at={(c9000 or {}).get('updated_at')} (want {_CREATE_UTC_S})",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # -- 4. watermark-regression probe: sync a THIRD time -------------------
    # Same container, same checkpoint, fault budget already spent (offset 0's
    # hit counter already reached its quota, so no more 500s) — a correct
    # connector's persisted watermark should not have moved backward, so this
    # pass must not re-crawl the whole table and must not poll from an
    # earlier-than-correct instant.
    pre_probe_log_len = len(handle.request_log())
    code, _out, err = ctx.app.run(["sync"])
    ctx.check(
        "incr_regression_probe_exit_ok",
        code == 0,
        f"exit={code} stderr={err[:400]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # Replaces the `probe_store == cp1` blob compare
    # (`incr_regression_probe_store_unchanged`) with a per-row, per-field diff.
    # A third sync against an unchanged checkpoint must be a no-op; a connector
    # whose watermark regressed re-applies the mutation timeline and lands
    # different `updated_at` stamps, which only a per-field diff can name.
    probe_store = dump_store(ctx)
    probe_diffs = row_diff(probe_store, cp1)
    ctx.check("incr_regression_probe_rows_exact",
        not probe_diffs,
        diff_detail("candidates@probe", probe_store, cp1, probe_diffs),
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    full_log = handle.request_log()
    probe_log = full_log[pre_probe_log_len:]
    probe_candidate_gets = [
        e for e in probe_log if e.get("method") == "GET" and e.get("path") == "/v1/candidates"
    ]
    regressed = [
        e for e in probe_candidate_gets
        if (ms := _modified_since_utc_s(e)) is not None and ms < _UPDATE_UTC_S
    ]
    ctx.check("watermark_no_regression_after_retry",
        len(probe_candidate_gets) < 60 and not regressed,
        f"probe_candidate_gets={len(probe_candidate_gets)} regressed={len(regressed)} "
        f"(a full re-crawl is >= 60 pages; a regressed modified_since is < {_UPDATE_UTC_S})",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # Regression guardrail (spec §3a): this task stays v1-only, across the
    # fault-pressured poll AND the regression probe.
    assert_gh_v2_disabled(ctx, full_log, label="incremental")

    await builtin_l2(ctx)
