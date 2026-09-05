"""task-0013 — parity_probe_targeted_repair.

One nightly pass of Harbour Talent Group's RecruitOS -> warehouse job, against a
warehouse snapshot the PREVIOUS run left in a state nothing upstream agrees
with.

The connector shipped and works. It is a webhook listener plus a nightly pass
that asks RecruitOS for `modified_since=<the position the last run recorded>`
and folds what comes back into the warehouse file. That design is what the data
platform's own desk note prescribes, and it is unsafe in a way that only shows
up when you compare the two systems end to end — which, as the note says in its
own words, "nobody has ever" done.

`docs/` holds RecruitOS's own documentation, byte-identical to the vendor bundle
(RecruitOS is a zero-lie control vendor; `check_honest_vendor_docs.py --enforce`
must keep reporting exactly one contaminated task, and it is not this one), plus
ONE task-local document — `docs/harbour-parity-desk-note.md`, the data
platform's internal note, disclaimed in its second line as "our own note, not
RecruitOS's documentation. Last revised in November." The vendor is honest; the
note is where this tenant's beliefs live, and three of them are false.

The snapshot and every expected divergence below are MEASURED by
`tools/rework/gen_answer_key_0139.py`, which boots RecruitOS at CHECKPOINT 0 and
at CHECKPOINT 53, crawls both, and derives the damage and the truth from what it
observed. Nothing here is asserted by hand.

  D1. **THE RECORDED POSITION IS A CLAIM, NOT EVIDENCE.** The desk note: *"That
      stamp is the pass's position, and it is authoritative: it is written by
      the same code that wrote the rows, so the two agree by construction ...
      A record whose `updated_at` we already hold cannot have changed."* The
      snapshot records `synced_through: 2026-03-14T11:20:00Z`. Its CONTENTS are
      a run that applied timeline entries 0-9 and 45-51 and nothing else. So the
      recorded position is later than most of what was left undone, and an
      incremental read anchored on it never asks about it. MEASURED: 49 of the
      76 divergences are invisible to `modified_since=synced_through`.

  D2. **THE SNAPSHOT'S OWN NEWEST STAMP IS WORSE, NOT BETTER.** The obvious
      repair for D1 — recompute the position from the file instead of trusting
      the number written in it — is a trap, because the previous run's coverage
      is not a prefix: it applied part of the tail (entries 45-51, stamps up to
      `11:52:00Z`) before it applied the middle. MEASURED: a position derived
      from the snapshot's newest `updated_at` finds **1** of the 76 divergences.
      There is no position that works. The only thing that works is comparing.

  D3. **PARITY IS THE VALUE, NOT THE TIMESTAMP.** 30 of the snapshot's rows
      carry the timestamp RecruitOS currently reports for them and the wrong
      canonical value — the shape a run that writes the stamp before the value
      and dies leaves behind. A full crawl that decides parity by comparing
      `updated_at` repairs everything else and cannot see any of these.
      MEASURED: 46 of 76.

  D4. **RETIRED RECORDS ARRIVE LOOKING ORDINARY, AND ARE INSIDE `total`.**
      RecruitOS serves soft-deleted records inline with `is_deleted: true`
      (`docs/index.md`, `docs/entities.md`), so 7 of the divergences are records
      the warehouse must stop holding, and the census the data platform is given
      is NOT the envelope's `total`. The desk note says the opposite in as many
      words: *"RecruitOS's `total` is the count of live records ... do not count
      the rows yourself."* MEASURED at this checkpoint: `total` is 259/46/300
      and the live counts are 253/43/300 — the applications collection agrees,
      which is the control that stops "always subtract something" from working.

  D5. **A WAREHOUSE ROW CAN NAME AN ID RECRUITOS HAS NEVER ISSUED.** The desk
      note: *"a warehouse row without a match upstream is not a thing that can
      happen."* Four of them are in the file. They are the one class no
      incremental read of any kind can reach, because RecruitOS will never
      mention an id it does not have.

The webhook surface is real but is NOT the mechanic. RecruitOS dispatches the
one event its boot checkpoint produces (`evt_00053`,
`application.stage_changed` for `app_0011` — derived here from the vendor's own
`build_delivery_plan`, not restated) plus, under `TAMPER_INJECT`, a
zero-signature copy timestamped 100000s in the past and delivered FIRST. The
listener has to accept the first and refuse the second, and the pass has to
report what it accepted. `builtin_l2`'s two webhook hard gates read the delivery
log's own response codes.

MEASURED (`tools/rework/probe/sweep.py`): see the task's WORKLOG entry for the
current numbers. `harden` is headroom-only; the shipped starter is a working
connector and scores well above zero by construction.

Evidence: every check reads the connector's declared artifacts against
`verifier/fixtures/answer_key.json`, the vendor's delivery log, or the vendor's
request log. `builtin_l2` fires once, immediately after the one app run, before
this verifier issues anything.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _webhook_family import (  # noqa: E402
    acked_event_ids,
    drain_webhooks,
    h2_tampered_rejected,
    serve_start,
    serve_stop,
)

VENDOR = "recruitos"
REPORT_COLUMNS = ["entity", "record_id", "divergence", "mirror_value", "vendor_value"]

# Both budgets are small on purpose. A submission with no listener must not turn
# the probe into a wall-clock endurance test: a working listener answers the
# in-vendor readiness probe in well under a second, and the boot's two
# deliveries land inside two.
_LISTENER_TIMEOUT_S = 15.0
_DRAIN_TIMEOUT_S = 20.0


def _read_json(ctx, name: str) -> Any:
    path = Path(ctx.output_dir) / name
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _read_report(ctx) -> list[dict[str, str]]:
    path = Path(ctx.output_dir) / "import_report.csv"
    if not path.is_file():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames != REPORT_COLUMNS:
                return []
            return [dict(row) for row in reader]
    except OSError:
        return []


def _row_key(row: dict[str, str]) -> str:
    return f"{(row.get('entity') or '').strip()}:{(row.get('record_id') or '').strip()}"


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _divergence_check(want: dict[str, str], got: dict[str, str] | None) -> tuple[bool, str]:
    who = f"{want['entity']} {want['record_id']} ({want['divergence']})"
    if got is None:
        return False, f"{who}: the pass never reported this record"
    wrong = [
        f"{column}={str(got.get(column) or '').strip()!r} (want {want[column]!r})"
        for column in ("divergence", "mirror_value", "vendor_value")
        if str(got.get(column) or "").strip() != want[column]
    ]
    if wrong:
        return False, f"{who}: " + ", ".join(wrong)
    return True, f"{who}: repaired and reported"


async def run(ctx) -> None:
    key = json.loads((Path(ctx.fixtures) / "answer_key.json").read_text(encoding="utf-8"))
    divergences: list[dict[str, str]] = key["divergences"]
    expected_events: list[dict[str, str]] = key["expected_events"]

    # -- the listener, and the deliveries this boot produces ------------------
    serve_start(ctx, vendor=VENDOR, timeout_s=_LISTENER_TIMEOUT_S)
    drained, deliveries = drain_webhooks(
        ctx,
        vendor=VENDOR,
        expect_events=[e["event_id"] for e in expected_events],
        timeout_s=_DRAIN_TIMEOUT_S,
    )
    listener_acked = bool(acked_event_ids(deliveries))
    serve_stop(ctx, vendor=VENDOR)

    # NOT `h6_deliveries_observed(deliveries)`: RecruitOS's dispatcher writes a
    # delivery-log row per ATTEMPT whether or not anything is listening, so
    # "deliveries exist" is a property of the vendor, not of the submission —
    # measured, the harness stub banked it. The evidence that matters is
    # deliveries the listener ANSWERED, which is also what makes the two checks
    # below claims about verification rather than about being unreachable.
    answered = [d for d in deliveries if d.get("status_code") is not None]
    ctx.check_l1(
        "webhook_deliveries_were_answered_by_the_listener",
        len(answered) >= 2,
        f"{len(answered)} of {len(deliveries)} delivery attempt(s) got a response from the "
        "listener; the signed one and the forged one both have to be answered for signature "
        "handling to be measurable",
    )
    ctx.check_l1(
        "webhook_event_acked_by_the_listener",
        drained,
        f"expected {[e['event_id'] for e in expected_events]} acked with a 2xx; "
        f"acked={sorted(acked_event_ids(deliveries))}",
    )
    ctx.check_l1(
        "webhook_forged_delivery_refused",
        *h2_tampered_rejected(deliveries, listener_acked=listener_acked),
    )

    # -- the nightly pass ----------------------------------------------------
    code, _out, err = ctx.app.run(["sync"])
    result = _read_json(ctx, "result.json")
    events = _read_json(ctx, "events.json")
    report = _read_report(ctx)
    reported = {_row_key(row): row for row in report}
    expected_report = [
        {column: str(row.get(column) or "") for column in REPORT_COLUMNS}
        for row in divergences
    ]

    ctx.check_l1(
        "parity_pass_completed",
        code == 0 and isinstance(result, dict) and bool(report)
        and report == expected_report and len(reported) == len(report),
        f"exit={code} result={type(result).__name__} report_rows={len(report)} "
        f"stderr={err[:300]}",
    )

    # Conduct, over the connector's own traffic and nothing else: this verifier
    # has issued no request to RecruitOS at any point.
    await builtin_l2(ctx, app_runs=1)

    # -- one check per divergence the warehouse was carrying ------------------
    for want in divergences:
        ctx.check_l1(
            f"divergence_{want['entity']}_{want['record_id']}",
            *_divergence_check(want, reported.get(f"{want['entity']}:{want['record_id']}")),
        )

    expected_keys = set(key["divergence_keys"])
    spurious = sorted(k for k in reported if k not in expected_keys)
    ctx.check_l1(
        "no_record_reported_that_was_already_in_step",
        bool(report) and not spurious,
        (
            f"{len(spurious)} record(s) reported as divergent that the warehouse and "
            f"RecruitOS already agreed on: {spurious[:6]}"
            if spurious
            else (
                f"{len(report)} row(s), none spurious"
                if report
                else "the pass reported nothing at all"
            )
        ),
    )

    counts = (result or {}).get("divergences") if isinstance(result, dict) else None
    want_counts = key["divergence_counts"]
    ctx.check_l1(
        "divergence_counts_match_the_repair",
        isinstance(counts, dict)
        and set(counts) == set(want_counts)
        and all(_as_int(counts.get(k)) == v for k, v in want_counts.items()),
        f"result.json divergences={counts}, expected {want_counts}",
    )

    # -- the census the data platform is handed ------------------------------
    census = (result or {}).get("census") if isinstance(result, dict) else None
    result_shape_ok = (
        isinstance(result, dict)
        and set(result) == {"source", "snapshot_synced_through", "divergences", "census"}
        and result.get("source") == "recruitos"
        and result.get("snapshot_synced_through") == key["synced_through"]
        and isinstance(census, dict)
        and set(census) == set(key["census"])
    )
    for collection, want in sorted(key["census"].items()):
        got = (census or {}).get(collection) if isinstance(census, dict) else None
        ctx.check_l1(
            f"census_{collection}",
            result_shape_ok and _as_int(got) == int(want),
            f"census[{collection}]={got}, expected {want} live record(s) "
            f"(the envelope's total is {key['envelope_totals'][collection]})",
        )

    # -- what the listener let through ---------------------------------------
    applied = (events or {}).get("applied") if isinstance(events, dict) else None
    applied_rows = [r for r in applied if isinstance(r, dict)] if isinstance(applied, list) else []
    got_events = sorted(
        (str(r.get("event_id")), str(r.get("event")), str(r.get("entity_id")))
        for r in applied_rows
    )
    want_events = sorted(
        (e["event_id"], e["event"], e["entity_id"]) for e in expected_events
    )
    ctx.check_l1(
        "events_json_lists_exactly_the_accepted_events",
        isinstance(events, dict) and set(events) == {"applied"}
        and isinstance(applied, list)
        and len(applied_rows) == len(applied)
        and all(set(r) == {"event_id", "event", "entity_id"} for r in applied_rows)
        and got_events == want_events,
        f"events.json applied={got_events}, expected {want_events}",
    )

    # -- the probe's own traffic ---------------------------------------------
    request_log = ctx.vendor(VENDOR).request_log()
    list_paths = set(key["list_paths"].values())
    listed = {
        entry.get("path")
        for entry in request_log
        if (entry.get("method") or "").upper() == "GET" and entry.get("path") in list_paths
    }
    ctx.check_l1(
        "every_mirrored_collection_was_read_this_pass",
        listed == list_paths,
        f"the pass listed {sorted(listed)}; the warehouse mirrors {sorted(list_paths)}",
    )
    throttled = [entry for entry in request_log if entry.get("rate_limited")]
    ctx.check_l1(
        "parity_pass_fits_the_published_request_budget",
        listed == list_paths and not throttled,
        (
            f"{len(throttled)} request(s) were throttled during the pass"
            if throttled
            else (
                f"{sum(1 for e in request_log if (e.get('method') or '').upper() == 'GET')} "
                "GET(s), none throttled"
                if listed == list_paths
                else "the pass did not read every mirrored collection, so a small "
                     "request count proves nothing"
            )
        ),
    )
