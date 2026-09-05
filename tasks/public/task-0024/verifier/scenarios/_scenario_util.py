"""Shared helpers for the task-0024 (TalentLoop selective-subscription)
scenarios. See task-0023's `_scenario_util.py` for the full rationale on the
serve-as-a-service / drain-webhooks mechanics -- this is the same pattern
extended to all 4 entities.
"""

from __future__ import annotations

import json
import time
from typing import Any

from bench.verifier.io import read_json_output

APP_SERVICE = "app"
VENDOR = "talentloop"
_DRAIN_TIMEOUT_S = 15.0
_DRAIN_POLL_S = 0.25

_OUTPUT_NAMES = ("candidates.json", "jobs.json", "applications.json", "notes.json")


def _stack(ctx):
    return ctx.app._stack


def reset_store(ctx) -> None:
    """Drop the canonical sqlite DB so each scenario starts empty.

    Scenarios share one DB file on the ``canonical-data`` volume for the whole
    grade; without this, tombstones/watermarks from an earlier scenario leak.
    """
    from bench.canonical_sqlite import reset_canonical_on_stack

    reset_canonical_on_stack(_stack(ctx))



def clear_outputs(ctx) -> None:
    for name in _OUTPUT_NAMES:
        try:
            (ctx.output_dir / name).unlink()
        except OSError:
            pass


def dump_store(ctx) -> tuple[list, list, list, list] | None:
    """Snapshot the canonical store to output/*.json and read it back as
    ``(candidates, jobs, applications, notes)``."""
    clear_outputs(ctx)
    code, _stdout, _stderr = ctx.app.run(["dump"])
    if code != 0:
        return None
    results = []
    for name in _OUTPUT_NAMES:
        data = read_json_output(ctx.output_dir / name, timeout_s=15.0)
        if data is None:
            return None
        results.append(data)
    return tuple(results)


def load_fixture(ctx, name: str) -> Any:
    return json.loads((ctx.fixtures / name).read_text(encoding="utf-8"))


def serve_start(ctx) -> None:
    stack = _stack(ctx)
    stack.up(service=APP_SERVICE, force_recreate=True)
    _wait_listener(stack)


def serve_stop(ctx) -> None:
    stack = _stack(ctx)
    try:
        stack.stop_service(APP_SERVICE)
    except Exception:
        pass


def _wait_listener(stack, timeout_s: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        probe = stack.exec(
            "vendor", "python", "-c",
            "import urllib.request,sys;"
            "sys.exit(0 if urllib.request.urlopen('http://connector:4000/',timeout=2).status==200 else 1)",
            check=False,
        )
        if probe.returncode == 0:
            return
        time.sleep(0.5)


def _is_2xx(code: Any) -> bool:
    try:
        return 200 <= int(code) < 300
    except (TypeError, ValueError):
        return False


def _rejected(code: Any) -> bool:
    if code is None:
        return False
    try:
        return not (200 <= int(code) < 300)
    except (TypeError, ValueError):
        return False


def drain_webhooks(
    ctx,
    *,
    expect_events: set[str],
    expect_tampered: bool = False,
    timeout_s: float = _DRAIN_TIMEOUT_S,
) -> bool:
    handle = ctx.vendor(VENDOR)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        deliveries = handle.webhook_deliveries()
        acked = {
            d.get("event_id")
            for d in deliveries
            if _is_2xx(d.get("status_code")) and not d.get("tampered")
        }
        events_ok = expect_events.issubset(acked)
        tamper_ok = True
        if expect_tampered:
            tamper_ok = any(
                d.get("tampered") and _rejected(d.get("status_code")) for d in deliveries
            )
        if events_ok and tamper_ok:
            time.sleep(0.3)
            return True
        time.sleep(_DRAIN_POLL_S)
    return False


def drain_checkpoint_events(
    ctx,
    steps: list[tuple[int, set[str]]],
    *,
    expect_tampered_at: set[int] | None = None,
    timeout_s: float = _DRAIN_TIMEOUT_S,
) -> tuple[bool, list[dict[str, Any]]]:
    """Recreate the vendor through each ``(checkpoint, expected_event_ids)``
    step IN ORDER, draining after each individual recreate.

    The dispatcher only ever queues events for the single half-open window
    ``(checkpoint-1, checkpoint]`` on a given boot (see
    talentloop/webhooks.py's ``build_delivery_plan``) -- never cumulative --
    and every boot also truncates ``webhook_deliveries.jsonl`` to empty. A
    single jump straight to the final checkpoint therefore only ever
    delivers that LAST mutation's event; collecting events for mutations
    spread across several checkpoints requires one recreate+drain cycle per
    checkpoint. This helper does that and concatenates each step's delivery
    log (read right after that step's drain, before the next recreate wipes
    it) so callers can still inspect the full cross-checkpoint history
    afterwards.
    """
    handle = ctx.vendor(VENDOR)
    all_deliveries: list[dict[str, Any]] = []
    all_ok = True
    for cp, expect_events in steps:
        handle.recreate(checkpoint=cp)
        want_tamper = expect_tampered_at is None or cp in expect_tampered_at
        ok = drain_webhooks(
            ctx, expect_events=expect_events, expect_tampered=want_tamper, timeout_s=timeout_s
        )
        all_ok = all_ok and ok
        all_deliveries.extend(handle.webhook_deliveries())
    return all_ok, all_deliveries


# ---------------------------------------------------------------------------
# Answer-key comparison
# ---------------------------------------------------------------------------

def row_diff(store: list | None, want: list) -> list[dict]:
    """Per-source_id, per-field comparison against an answer key.

    The targeted replacement for this task's twelve deleted blob compares:
    `backfill_candidates_match_fixture`, `backfill_jobs_match_fixture`,
    `backfill_applications_match_fixture`, `backfill_notes_match_fixture`,
    `freshness_candidates_match_fixture`, `freshness_applications_match_fixture`,
    `poll_recur_candidates_match_fixture`, `poll_recur_jobs_match_fixture`,
    `poll_recur_applications_match_fixture`, `poll_recur_notes_match_fixture`,
    `tamper_candidates_match_fixture` and `tamper_applications_match_fixture`.
    After the deletion the three
    scenarios graded five individual facts between them (three webhook-applied
    rows, plus job_0003's status and note_0004's body), and nothing else: no
    entity's store was checked as a whole in any phase, in a task whose whole
    subject is UNIFIED freshness across four entities and two discovery paths.

    Order-insensitive by source_id: dump order is not part of the contract.
    """
    if store is None:
        return [{"source_id": "<no output>", "field": "<store unreadable>"}]
    got_by_id = {r.get("source_id"): r for r in store}
    want_by_id = {r.get("source_id"): r for r in want}
    diffs: list[dict] = []
    for sid in sorted(set(want_by_id) | set(got_by_id), key=str):
        w, g = want_by_id.get(sid), got_by_id.get(sid)
        if g is None:
            diffs.append({"source_id": sid, "field": "<missing row>"})
            continue
        if w is None:
            diffs.append({"source_id": sid, "field": "<unexpected row>"})
            continue
        for key in sorted(set(w) | set(g)):
            if w.get(key) != g.get(key):
                diffs.append({"source_id": sid, "field": key,
                              "want": w.get(key), "got": g.get(key)})
    return diffs


def diff_detail(label: str, store: list | None, want: list,
                diffs: list[dict], limit: int = 3) -> str:
    n = "none" if store is None else len(store)
    if not diffs:
        return f"{label}: {n} row(s), every field matches the answer key"
    shown = json.dumps(diffs[:limit], sort_keys=True, default=str)
    more = f" (+{len(diffs) - limit} more)" if len(diffs) > limit else ""
    return f"{label}: rows={n} expected={len(want)}; {len(diffs)} field diff(s): {shown}{more}"


def grade_cp0_backfill(ctx, dumped: tuple) -> None:
    """The four cp0 answer-key comparisons, shared by the two scenarios that
    back-fill (identical names and identical values in both, as the scorer
    collapses repeated names to one worst-case instance).

    +1, not 0/-1: this task's `poll.py` is entirely IMPLEMENT ME (`run_poll` and
    `_poll_one_kind` both raise), and `backfill` shares that code, so the empty
    probe cannot back-fill anything at all — every one of these is honestly
    discriminating rather than masked by an unrelated crash.
    """
    candidates, jobs, applications, notes = dumped
    for entity, store in (
        ("candidates", candidates), ("jobs", jobs),
        ("applications", applications), ("notes", notes),
    ):
        want = load_fixture(ctx, f"{entity}_checkpoint_0.json")
        diffs = row_diff(store, want)
        ctx.check(
            f"backfill_{entity}_rows_exact",
            not diffs,
            diff_detail(f"{entity}@cp0", store, want, diffs),
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )
