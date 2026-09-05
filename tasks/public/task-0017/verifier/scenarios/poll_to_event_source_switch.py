"""task-0017 -- poll_to_event_source_switch (TalentForge, migrate).

Brightmoor's nightly candidate mirror is being moved off a whole-collection
walk and onto TalentForge's event subscription. The connector in `repo/` is the
WORKING legacy poller, not a stub: it logs in, lists every candidate, rebuilds
`mirror.json` and exits 0. It has no receiver, no ledger and no incremental
pass. That is the migration.

THE WALK (one scenario, seventeen vendor epochs)
------------------------------------------------
  CP20               boot default. `sync` runs cold -> full back-fill, 401
                     candidates. Every variant can do this; nothing is
                     announced yet because the receiver is not up.
  CP21..CP35, CP65   the vendor is recreated once per step and the receiver is
                     brought up inside the first of them. TalentForge's
                     dispatcher queues ONLY the
                     half-open window (CHECKPOINT-1, CHECKPOINT] on a boot and
                     truncates its delivery log each time, so each step
                     announces exactly one candidate change: 16 events, of
                     which evt_00022 and evt_00025 are re-delivered, and every
                     boot also injects one zero-signature, 100000s-stale copy.
  CP36..CP64         NEVER BOOTED. Their 29 changes (cand_0366..cand_0380,
                     cand_0131..cand_0144) were announced to nobody. They are
                     in the CP65 world and reachable only by asking for them.
  CP65               receiver stopped; `sync` runs warm -> the cut-over pass
                     the whole task grades.

The candidate id set is identical at CP20 and CP65 (401 both times) and every
one of the 45 changed records moves BOTH `pipeline_status` and `modified_at`
and nothing else -- verified by tools/rework/gen_answer_key_0158.py, which
refuses to write a key otherwise. So staleness here is invisible to any
cardinality or membership check: only the per-record field sweep sees it.

THE DEVICES
-----------
D1  BELIEF vs WIRE (task-local, attributed). `docs/brightmoor-cutover-
    runbook.md` is Brightmoor's own note -- named author, dated, and it says
    in its own second line that it is not TalentForge documentation. It
    asserts two things the sandbox contradicts:

      (a) "the payload's `data` object is the candidate record, in the same
          shape the list route serves it ... there is no follow-up read". The
          wire's `data` is `{"id": "cand_NNNN"}` and nothing else -- one
          printed delivery reveals it. A connector that applies it puts a row
          with no current values into the mirror and a ledger row with no
          `pipeline_status`.
      (b) "once the subscription is live the stream is the source of truth ...
          take the full walk out in the same change". The 29 blackout changes
          produced no delivery at all, so a connector that retires the list
          route holds them at their pre-cut-over values for ever.

    `docs/webhooks.md` states the opposite, truthfully and unprompted ("pair
    webhook consumption with periodic polling (`modified_since`)
    reconciliation so a lost event is caught on the next poll cycle"), which
    is what makes this a stale-belief divergence rather than withheld
    information. The vendor is honest here; the internal note is six months
    old and was written about a quieter tenant.

D2  DOC LIE, candidate stamp name AND type (LIES.md #1 + #2). docs/entities.md
    types the candidate's last-modified field `updatedAt`, `string (ISO
    8601)`; the wire carries `modified_at`, a bare epoch-millisecond integer.
    `store.normalise_updated_at` is docs-faithful by construction
    (AUTHORING-BRIEF: "where the docs are wrong the given code is wrong too"),
    so the given connector writes `null` into the `updated_at` column of every
    row of both artifacts. Graded on all 45 changed records AND on a 20-record
    sample of records nothing touched, because the divergence corrupts the
    whole mirror rather than only the part the cut-over moved.

D3  COMPETENCE, re-delivery. evt_00022 and evt_00025 each arrive twice
    (seeded, rate 0.2). Both artifacts are keyed, so collapsing them is cheap
    -- carried as one invariant, not padded out.

WHY THE STARTER AND THE NAIVE FAIL DIFFERENT HALVES
---------------------------------------------------
On this vendor a full crawl is strictly MORE powerful than the stream: the
poll surface loses nothing, so a `migrate` task here cannot get its floor
separation from the current-state column alone. It does not have to. The two
wrong answers are opposed:

  starter  the legacy walk. Gets `pipeline_status` right on all 45 changed
           records -- and has no receiver (so nothing is ever acked, the
           forged copy is never refused, the re-delivery is never collapsed),
           no ledger row anywhere, no narrowed pass, and `watermark: null`
           because it reads the stamp by its documented name.
  naive    the runbook-faithful cut-over. Stands the receiver up correctly
           from docs/webhooks.md, keys the ledger by `event_id`, applies
           `data` straight in and stamps `occurred_at` -- so it gets every
           ledger identity row and the announced half of the stamps right,
           and every current value in the mirror wrong, plus the 29 blackout
           records held at their CP20 values for ever.

Neither can pass both halves without doing the migration.

MEASURED (tools/rework/probe/sweep.py + score.py; one scenario, 156 checks:
152 L1 + 4 conduct)
--------------------------------------------------------------------------
    gold                                            156/156  1.000
    starter (the legacy nightly walk)                53/156  0.340
    stub    (harness stub patch, RAN)                 0/156  0.000
    naive   (runbook-faithful cut-over)              41/156  0.263
    basin: defensive   (naive + the stamp fix its
            own null-filled output screams for)      61/156  0.391
    basin: second-guess (that, plus reading the
            record the announcement names -- but
            still no re-poll, per the runbook)       96/156  0.615
    alt-correct (re-poll first, index it, source
            the ledger from the polled records,
            walk the events in reverse)            156/156  1.000

    migrate floor rule: floor 0.340 <= 0.400 - headroom 0.660 - disc 103
    vac 0/154 = 0.0% (the stub passes NOTHING) - ungrounded 0.0%

STARTER vs NAIVE differ on 86 checks, 49/37 by direction:
    starter wins 45x cutover_mirror_status_* plus all four conduct checks
                     (the naive never lists a collection in the cut-over
                     pass, so no_unnecessary_full_resync:candidate is never
                     emitted for it, and an unemitted check counts as failed)
    naive   wins 16x cutover_ledger_row_*, 16x cutover_mirror_stamp_* (the
                     announced half only -- `occurred_at` happens to be the
                     ISO rendering of the very stamp the docs mis-name and
                     mis-type), the three receiver invariants,
                     cutover_ledger_confined_to_announced_changes and
                     cutover_watermark_moved_with_the_mirror.

CONDUCT. `builtin_l2` is invoked ONCE, after the final pass, so it grades the
CP65 epoch. The vendor unlinks its request and token logs on every boot, so an
earlier call would grade evidence that no longer exists, and a call per
recreate would multiply conduct mass seventeen-fold into the floor. The
verifier injects no vendor traffic of its own anywhere in this scenario -- the
answer key is derived offline from `talentforge.state` -- so no exclusion
window is needed.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import _webhook_family as wf  # noqa: E402

from bench.verifier.builtin_l2 import builtin_l2  # noqa: E402

VENDOR = "talentforge"
APP_SERVICE = "app"
LIST_PATH = "/rest/candidates"

# Drain budgets. The first step also pays for the receiver's own boot; after a
# step has proven the receiver is not answering at all there is nothing to wait
# for, and burning a full budget per step would push a receiver-less
# submission (the starter, the stub) past the probe timeout.
_FIRST_DRAIN_S = 25.0
_STEP_DRAIN_S = 15.0
_DEAD_RECEIVER_DRAIN_S = 1.0
_POLL_S = 0.25
_SETTLE_S = 0.4


def _load_key(ctx) -> dict:
    return json.loads((ctx.fixtures / "answer_key.json").read_text(encoding="utf-8"))


def _read_rows(ctx, filename: str, key_field: str) -> dict[str, dict]:
    """`{key: row}` from one declared artifact, or {} if it is unusable."""
    path = Path(ctx.output_dir) / filename
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    rows = doc.get("rows") if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict] = {}
    for row in rows:
        if isinstance(row, dict) and row.get(key_field):
            out[str(row[key_field])] = row
    return out


def _read_doc(ctx, filename: str) -> dict:
    path = Path(ctx.output_dir) / filename
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return doc if isinstance(doc, dict) else {}


def _read_row_list(ctx, filename: str) -> list:
    path = Path(ctx.output_dir) / filename
    if not path.is_file():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return []
    rows = doc.get("rows") if isinstance(doc, dict) else doc
    return rows if isinstance(rows, list) else []


def _read_state(ctx) -> dict:
    path = Path(ctx.output_dir) / "state.json"
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return doc if isinstance(doc, dict) else {}


def _drain(vendor, expect_ids, timeout_s: float):
    """Wait until every expected event id carries a 2xx ack."""
    want = {str(e) for e in expect_ids}
    deadline = time.monotonic() + timeout_s
    deliveries: list[dict] = []
    while True:
        deliveries = vendor.webhook_deliveries()
        if want <= wf.acked_event_ids(deliveries):
            time.sleep(_SETTLE_S)
            return True, vendor.webhook_deliveries()
        if time.monotonic() >= deadline:
            return False, deliveries
        time.sleep(_POLL_S)


async def run(ctx) -> None:
    key = _load_key(ctx)
    vendor = ctx.vendor(VENDOR)
    stack = vendor._stack

    # -- phase 1: the cold back-fill, at the boot checkpoint -----------------
    code1, _out1, err1 = ctx.app.run(["sync"])
    backfill_rows = _read_rows(ctx, "mirror.json", "source_id")
    ctx.check_l1(
        "cutover_backfill_pass_completed",
        code1 == 0 and bool(backfill_rows),
        f"exit={code1} mirror_rows={len(backfill_rows)} stderr={err1[:400]}",
    )
    ctx.check_l1(
        "cutover_backfill_filled_the_cold_mirror",
        len(backfill_rows) == key["counts"]["backfill_rows"],
        f"held {len(backfill_rows)} after the cold pass, expected "
        f"{key['counts']['backfill_rows']}",
    )

    # -- phase 2: receiver up, one boot per announced change -----------------
    #
    # The receiver comes up AFTER the first recreate, not before. The boot
    # checkpoint has a dispatch window of its own and its dispatcher retries a
    # refused delivery for up to 90s, so a receiver started while that boot is
    # still alive would be handed an event this walk never announced -- and
    # whether it arrived would depend on how long the back-fill took. The first
    # recreate kills that process, after which the only thing in flight is the
    # step's own event, which the dispatcher happily retries until the receiver
    # answers.
    deliveries: list[dict] = []
    receiver_live = True
    for i, step in enumerate(key["announced_events"]):
        vendor.recreate(checkpoint=step["checkpoint"])
        if i == 0:
            stack.up(service=APP_SERVICE, force_recreate=True)
            time.sleep(1.5)
        if receiver_live:
            budget = _FIRST_DRAIN_S if i == 0 else _STEP_DRAIN_S
        else:
            budget = _DEAD_RECEIVER_DRAIN_S
        ok, step_deliveries = _drain(vendor, [step["event_id"]], budget)
        if i == 0 and not ok:
            # Nothing answered the very first dispatch inside a budget that
            # also covers the receiver's boot. Later steps get a token wait so
            # the run still terminates; the acked-events invariant below is
            # what records the failure.
            receiver_live = False
        deliveries += step_deliveries

    wf.serve_stop(ctx, vendor=VENDOR)

    # -- phase 3: the cut-over pass -----------------------------------------
    code2, _out2, err2 = ctx.app.run(["sync"])
    mirror = _read_rows(ctx, "mirror.json", "source_id")
    ledger_rows = _read_row_list(ctx, "change_ledger.json")
    ledger = _read_rows(ctx, "change_ledger.json", "event_id")
    state = _read_state(ctx)
    mirror_doc = _read_doc(ctx, "mirror.json")
    ledger_doc = _read_doc(ctx, "change_ledger.json")

    ctx.check_l1(
        "cutover_pass_completed",
        code2 == 0 and bool(mirror),
        f"exit={code2} mirror_rows={len(mirror)} stderr={err2[:400]}",
    )
    snapshot = key["final_snapshot"]
    expected_mirror = {
        cid: {
            "source_id": cid,
            "given_name": want["given_name"],
            "family_name": want["family_name"],
            "email": want["email"],
            "phone": want["phone"],
            "pipeline_status": want["pipeline_status"],
            "is_deleted": want["is_deleted"],
            "updated_at": want["updated_at"],
        }
        for cid, want in snapshot.items()
    }
    mirror_contract_ok = (
        set(mirror_doc) == {"row_count", "rows"}
        and type(mirror_doc.get("row_count")) is int
        and mirror_doc.get("row_count") == len(key["all_ids"])
        and len(_read_row_list(ctx, "mirror.json")) == len(key["all_ids"])
        and mirror == expected_mirror
    )
    ctx.check_l1(
        "cutover_mirror_holds_every_candidate",
        mirror_contract_ok,
        f"mirror holds {len(mirror)} id(s); missing="
        f"{sorted(set(key['all_ids']) - set(mirror))[:5]} "
        f"unexpected={sorted(set(mirror) - set(key['all_ids']))[:5]}",
    )

    # -- the mirror, per record ---------------------------------------------
    stale = key["backfill_snapshot"]
    for cid in key["changed_ids"]:
        want = snapshot[cid]
        row = mirror.get(cid) or {}
        got = row.get("pipeline_status")
        ctx.check_l1(
            f"cutover_mirror_status_{cid}",
            got == want["pipeline_status"],
            f"{cid}: pipeline_status={got!r}, expected {want['pipeline_status']!r} "
            f"(it was {stale[cid]['pipeline_status']!r} when the mirror was filled)",
        )
    for cid in key["changed_ids"]:
        want = snapshot[cid]
        row = mirror.get(cid) or {}
        got = row.get("updated_at")
        ctx.check_l1(
            f"cutover_mirror_stamp_{cid}",
            got == want["updated_at"],
            f"{cid}: updated_at={got!r}, expected {want['updated_at']!r} "
            f"(the wire's own value for this record is {want['modified_at_ms']})",
        )
    for cid in key["untouched_sample"]:
        want = snapshot[cid]
        row = mirror.get(cid) or {}
        got = row.get("updated_at")
        ctx.check_l1(
            f"cutover_mirror_stamp_untouched_{cid}",
            got == want["updated_at"],
            f"{cid}: updated_at={got!r}, expected {want['updated_at']!r} -- nothing "
            f"changed about this person, the column is simply wrong on every row",
        )

    # -- the change ledger, per announced change ----------------------------
    announced_ids = {step["event_id"] for step in key["announced_events"]}
    for step in key["announced_events"]:
        evt = step["event_id"]
        matching = [r for r in ledger_rows if str(r.get("event_id")) == evt]
        row = matching[0] if matching else {}
        ok = (
            len(matching) == 1
            and set(row) == {
                "event_id", "event", "candidate_id", "occurred_at",
                "pipeline_status", "updated_at",
            }
            and str(row.get("event")) == step["event"]
            and str(row.get("candidate_id")) == step["candidate_id"]
            and str(row.get("occurred_at")) == step["occurred_at"]
        )
        ctx.check_l1(
            f"cutover_ledger_row_{evt}",
            ok,
            f"{evt}: {len(matching)} row(s); got event={row.get('event')!r} "
            f"candidate_id={row.get('candidate_id')!r} "
            f"occurred_at={row.get('occurred_at')!r}, expected {step['event']!r} / "
            f"{step['candidate_id']!r} / {step['occurred_at']!r}",
        )
    for step in key["announced_events"]:
        evt = step["event_id"]
        cid = step["candidate_id"]
        want = snapshot[cid]
        row = ledger.get(evt) or {}
        got_status = row.get("pipeline_status")
        got_stamp = row.get("updated_at")
        ctx.check_l1(
            f"cutover_ledger_values_{evt}",
            got_status == want["pipeline_status"] and got_stamp == want["updated_at"],
            f"{evt} ({cid}): pipeline_status={got_status!r} updated_at={got_stamp!r}, "
            f"expected {want['pipeline_status']!r} / {want['updated_at']!r} -- the "
            f"announcement itself carries neither",
        )

    # Requirement-shaped and gated: a ledger nobody wrote proves nothing about
    # what does or does not belong on it.
    unannounced = sorted(set(ledger) - announced_ids)
    ledger_envelope_ok = (
        set(ledger_doc) == {"row_count", "rows"}
        and type(ledger_doc.get("row_count")) is int
        and ledger_doc.get("row_count") == len(key["announced_events"])
        and len(ledger_rows) == len(key["announced_events"])
    )
    ctx.check_l1(
        "cutover_ledger_confined_to_announced_changes",
        ledger_envelope_ok and set(ledger) == announced_ids and not unannounced,
        f"{len(ledger)} ledger row(s); {len(unannounced)} of them name a change "
        f"nothing announced: {unannounced[:5]}",
    )

    # -- the wire: was anything ever received, and was the pass incremental --
    acked = wf.acked_event_ids(deliveries)
    listener_acked = bool(acked)

    ok, detail = wf.h1_expected_events_acked(deliveries, announced_ids)
    ctx.check_l1("cutover_announced_events_acked", ok, detail)

    ok, detail = wf.h2_tampered_rejected(deliveries, listener_acked=listener_acked)
    ctx.check_l1("cutover_forged_delivery_refused", ok, detail)

    applied_counts = {
        evt: sum(1 for r in ledger_rows if str(r.get("event_id")) == evt)
        for evt in announced_ids
    }
    ok, detail = wf.h3_duplicates_collapsed(
        deliveries, applied_counts, listener_acked=listener_acked
    )
    ctx.check_l1("cutover_redelivery_collapsed", ok, detail)

    # The final epoch's request log holds the cut-over pass and nothing else:
    # `recreate(65)` unlinked it, and the verifier issues no vendor traffic.
    log = vendor.request_log()
    listed = [e for e in log if e.get("method") == "GET" and e.get("path") == LIST_PATH]
    narrowed = [e for e in listed if "modified_since" in (e.get("query") or {})]
    ctx.check_l1(
        "cutover_pass_listed_incrementally",
        bool(narrowed),
        f"{len(listed)} list request(s) in the cut-over pass, {len(narrowed)} of them "
        f"narrowed; queries={[e.get('query') for e in listed][:4]}",
    )

    watermark = state.get("watermark")
    lo = key["watermark"]["backfill_max_iso"]
    hi = key["watermark"]["final_max_iso"]
    ctx.check_l1(
        "cutover_watermark_moved_with_the_mirror",
        set(state) == {"watermark", "runs"}
        and state.get("runs") == 2
        and isinstance(watermark, str)
        and lo < watermark <= hi,
        f"state.json watermark={watermark!r}; the mirror was filled at {lo} and the "
        f"tenant's latest change is {hi}, so anything at or before {lo} says the "
        f"mirror never moved",
    )

    # Emit the four conduct names unconditionally. builtin_l2 has no evidence
    # to emit them for a submission that never contacts TalentForge, and an
    # absent check must score as a failure rather than leave the denominator.
    data_gets = [
        entry
        for entry in log
        if entry.get("method") == "GET"
        and str(entry.get("path") or "").startswith("/rest/")
    ]
    credential_keys = {"key", "api_key", "token", "secret", "password"}
    credential_query = [
        entry
        for entry in data_gets
        if credential_keys.intersection(
            str(name).lower() for name in (entry.get("query") or {})
        )
    ]
    ctx.check_l1(
        "no_credentials_in_query_string",
        bool(data_gets) and not credential_query,
        f"data_gets={len(data_gets)} credential_query={len(credential_query)}",
    )
    serialized_data_gets = json.dumps(data_gets, sort_keys=True)
    exposed_secrets = [
        value
        for value in ("talentforge-test-client_secret", "tf-test-webhook-secret")
        if value in serialized_data_gets
    ]
    ctx.check_l1(
        "no_secrets_echoed_to_vendor",
        bool(data_gets) and not exposed_secrets,
        f"data_gets={len(data_gets)} exposed_secret_values={len(exposed_secrets)}",
    )
    token_mints = [
        entry
        for entry in log
        if entry.get("method") == "POST" and entry.get("path") == "/oauth/token"
    ]
    ctx.check_l1(
        "reauth_per_request:/oauth/token",
        bool(data_gets) and 0 < len(token_mints) <= 8,
        f"data_gets={len(data_gets)} token_mints={len(token_mints)} ceiling=8",
    )
    ctx.check_l1(
        "no_unnecessary_full_resync:candidate",
        bool(listed)
        and all("modified_since" in (entry.get("query") or {}) for entry in listed),
        f"list_requests={len(listed)} narrowed={len(narrowed)}",
    )

    await builtin_l2(ctx, app_runs=2)
