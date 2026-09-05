"""task-0019 -- token_layer_rewrite_zero_downtime (Vettly).

One change-feed cycle across the cutover that switches vaultd off. vaultd was
Meridian's own OAuth broker, and it did three things: it held the Vettly grant,
it kept the feed's cursor, and it cleaned the payloads up before a connector
ever saw them. The grant half has already been rewritten into the connector --
that is the starter's transport, and it is complete and correct. The other two
stop the moment the broker does, which is what this cycle has to survive with
no gap and no double-count.

Every number below is measured by `tools/rework/gen_answer_key_0165.py` against
a live tenant before the key is written, and the generator REFUSES to write a
key if any device has gone inert -- an ISO watermark that started filtering, a
delta with fewer than twelve soft-deletes in it, or a delta whose rows could
all be joined without leaving the delta.

Difficulty devices, where each one bites, and how broadly it is asserted:

1. THE WATERMARK vaultd LEFT (vendor doc lie 2, in an expression none of the
   other vettly tasks use: a filter that SILENTLY DOES NOTHING). `docs/
   pagination.md` types `modified_since` as an ISO 8601 timestamp and `docs/
   entities.md` types every stamp the same way; on the wire every stamp is a
   bare epoch-SECONDS integer and `modified_since` is compared as one. A value
   Vettly cannot read as an integer is not rejected -- it is dropped, and the
   collection comes back whole. `docs/vaultd-decommission-note.md` is
   Meridian's OWN note, attributed as theirs and not Vettly's, and it says the
   broker stored the cursor in ISO 8601 "which is exactly the format Vettly's
   `modified_since` takes, so it goes straight back across the wire as it
   stands". Measured on the wire by the key generator:

       modified_since=<epoch seconds>   ->  11 subjects,  32 checks,  17 reports
       modified_since=<the ISO string>  -> 300 subjects, 400 checks, 250 reports
       modified_since=<epoch millis>    ->   0,           0,           0

   So the docs-faithful cycle re-delivers the entire tenant -- 950 records for
   a warehouse whose loader cannot take a row back -- and the obvious second
   guess after noticing the wire carries integers delivers nothing at all. The
   consequence is asserted on the twelve control records the warehouse already
   holds (in both artifacts), on the five tallies, on `record_count`, on
   `cursor_used`, and by `builtin_l2`'s own `no_unnecessary_full_resync`.
   35 checks.

2. THE FEED IS NO LONGER PRE-CLEANED (task-local belief-reality divergence).
   The same note says vaultd "dropped closed files before the feed ever saw
   them ... that is why the warehouse loader upserts blindly and has no
   retirement path of its own". Vettly's list surface does the opposite: a
   closed record stays in the collection carrying `is_deleted: true`, and its
   `updated_at` bumps when it closes, so 16 of the 60 records this cursor
   selects are retirements riding the delta -- six subjects and ten checks.
   A cycle that keeps the broker's belief upserts all sixteen and the
   warehouse resurrects them. Asserted three times over, because the decision
   propagates three times: on the entry's own `op`, on the loader row the
   warehouse actually applies, and on the purge list it runs afterwards.
   58 checks.

3. THE DELTA DOES NOT CARRY ITS OWN PARENTS (competence). The warehouse is
   keyed by person and the note says so plainly -- vaultd "stitched the person
   onto every event" and now the cycle must. Measured: all 32 moved checks
   name a subject that has not itself moved, and 16 of the 17 moved reports
   name a check that has not moved, so 49 of the 60 entries cannot be
   completed from the delta alone. A cycle that joins only what the delta
   handed it writes a null person on those 49 rows. 98 checks.

Measured (rig, 184 checks, 2026-08-11):
  gold 1.000 · starter 0.000 · stub 0.000 · naive 0.511
  floor 0.000 · headroom 1.000 · discriminating 184 · vac 0.0% · free 0.0%.
  Starter and naive differ on 94 checks, all 94 in the naive's favour and none
  in the starter's -- on a `migrate` task whose starter is the half-moved
  connector (transport rewritten, cycle not), the starter is strictly worse
  than the naive rather than a rival answer, so the opposed pair a `fix` task
  can build has nothing to hang on here.

naive.patch is the cycle a competent engineer writes from `docs/` and the
handover note: it hands the stored ISO cursor straight to `modified_since`, it
upserts everything because the broker's note says the feed is a live-records
surface, and it does do the person join (the note asks for it, so device 3 is
not what it fails on). It re-delivers all 950 records, retires none of the 16,
and fails all three collection-specific handover-watermark evidence checks.

Four further basins, measured the same way:
  * DEFENSIVE -- the naive plus the single most obvious guard, re-checking the
    server-side filter on our side. Written docs-faithfully it compares the
    record's stamp against the ISO cursor as the strings the entity reference
    says they are, which drops everything: 0.022. The fail-OPEN spelling of
    the same guard (parse both with `fromisoformat`, keep the row when the
    parse raises) is exactly the naive: 0.511. Neither guard finds the unit,
    which is the point -- both spellings of the check are written in the units
    the docs assert.
  * SECOND GUESS -- notices the wire carries integers and converts the cursor
    to epoch MILLISECONDS, the usual wire encoding: 0.022. The opposed basin
    to the naive's, and the reason the control family and the per-record
    family have to both exist.
  * WATERMARK SOLVED, BROKER'S BELIEF KEPT -- the right delta, every entry an
    upsert: 0.685. Solving device 1 outright is not enough on its own.
  * DELTA-ONLY JOIN -- devices 1 and 2 both solved, the person resolved from
    the delta alone: 0.467.
  * ALTERNATIVE CORRECT -- a structurally different build: the same narrowed
    collection surface, but `calendar.timegm` + `time.strptime` instead of
    `datetime`, an eager parent-closure plan instead of gold's lazy cache, and
    a multi-argument row builder instead of an object wrapper: 1.000. The
    checks grade the outcome, not this repository's route to it.

Every check here reads the connector's artifacts field-by-field against the
answer key's live-crawled truth, or the vendor's own request log. The request
log is snapshotted immediately after the app run and this verifier issues no
vendor traffic of its own, so nothing here can grade the verifier's own
requests.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

VETTLY = "vettly"
KINDS = ("subject", "check", "report")
COLLECTION = {"subject": "subjects", "check": "checks", "report": "reports"}
ENTRY_FIELDS = (
    "kind", "op", "subject_id", "subject_email", "updated_at", "detail",
)
RESULT_FIELDS = {"cursor_used", "next_cursor", "record_count", "counts", "retired_ids", "changes"}
CSV_FIELDS = ("kind", "op", "subject_id", "subject_email")


def _read_json(ctx, name: str):
    path = Path(ctx.output_dir) / name
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _read_csv(ctx, name: str) -> list[dict[str, str]]:
    path = Path(ctx.output_dir) / name
    if not path.is_file():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            return [dict(row) for row in csv.DictReader(fh)]
    except (OSError, csv.Error):
        return []


def _text(value) -> str:
    """One artifact field as a comparable string; null and empty are the same.

    An integer written as ``1`` and as ``"1"`` are the same watermark, so
    numeric values normalise through ``int`` -- the artifact is graded on the
    instant it carries, not on whether the writer quoted it. Anything that is
    not an integer (an ISO 8601 string, say) keeps its own spelling and will
    not compare equal to one.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return str(int(value))
    text = str(value).strip()
    try:
        return str(int(text))
    except ValueError:
        return text


async def run(ctx) -> None:
    key = json.loads(
        (Path(ctx.fixtures) / "answer_key.json").read_text(encoding="utf-8"))
    expected: dict[str, dict] = key["expected"]
    record_ids: list[str] = sorted(expected)
    retired_ids: set[str] = set(key["retired_ids"])
    not_retired_sample: list[str] = list(key["not_retired_sample"])
    controls: list[dict] = list(key["controls"])
    counts: dict[str, int] = key["counts"]

    # The grading harness deliberately boots every vendor at checkpoint 0;
    # task Compose's checkpoint is only the native-rig default.  Recreate the
    # exact live world recorded by the generated key before the connector runs
    # so native and container lanes exercise the same tenant.
    ctx.vendor(VETTLY).recreate(checkpoint=int(key["checkpoint"]))
    code, _out, err = ctx.app.run(["sync"])

    # Snapshot the vendor's log before anything else. This verifier issues no
    # vendor traffic at all, so every log-derived check below reads the
    # connector's conduct and only the connector's conduct.
    request_log = ctx.vendor(VETTLY).request_log()

    result = _read_json(ctx, "result.json")
    changes = (result or {}).get("changes")
    entry_by_id: dict[str, dict] = {}
    if isinstance(changes, list):
        for row in changes:
            if isinstance(row, dict) and row.get("record_id"):
                entry_by_id.setdefault(str(row["record_id"]), row)

    loader_rows = _read_csv(ctx, "import_report.csv")
    loader_by_id: dict[str, dict[str, str]] = {}
    for row in loader_rows:
        rid = (row.get("record_id") or "").strip()
        if rid:
            loader_by_id.setdefault(rid, row)

    reported_retired = (result or {}).get("retired_ids")
    retired_reported: set[str] = set()
    if isinstance(reported_retired, list):
        retired_reported = {str(x) for x in reported_retired}

    # Per-kind witness that the cycle did real work of that kind. Credit for
    # correctly NOT reporting a record the warehouse already holds requires
    # evidence that records of that kind were reported at all -- otherwise
    # "reported nothing" banks the whole control family for free
    # (AUTHORING-CHECKLIST: gate every check that credits ABSENCE). The witness
    # is membership of the expected id set, not merely a non-empty file, so one
    # accidental row cannot open the gate.
    reported_expected = {
        kind: {
            rid for rid in entry_by_id
            if rid in expected and expected[rid]["kind"] == kind
        }
        for kind in KINDS
    }

    # ---------------------------------------------------------------- L1 ---
    ctx.check_l1(
        "t0165_cutover_cycle_completed",
        code == 0 and isinstance(result, dict) and isinstance(changes, list),
        f"exit={code} result={type(result).__name__} "
        f"changes={type(changes).__name__} stderr={err[-400:]}",
    )

    # DEVICES 1 + 2 + 3, per record the tenant has moved since the handover
    # cursor: the entry's own account of the record, against what Vettly holds.
    for rid in record_ids:
        want = expected[rid]
        got = entry_by_id.get(rid)
        ok = bool(got) and all(
            _text((got or {}).get(field)) == _text(want.get(field))
            for field in ENTRY_FIELDS
        )
        if got:
            wrong = [
                f"{field}={got.get(field)!r} (want {want.get(field)!r})"
                for field in ENTRY_FIELDS
                if _text(got.get(field)) != _text(want.get(field))
            ]
            detail = (f"{rid}: " + ("; ".join(wrong) if wrong else "matches Vettly"))
        else:
            detail = f"{rid}: the change file carries no entry for it"
        ctx.check_l1(f"t0165_{rid}_change_entry_matches_vettly", ok, detail)

    # The same decision where the warehouse actually consumes it.
    for rid in record_ids:
        want = expected[rid]
        row = loader_by_id.get(rid)
        ok = bool(row) and all(
            _text((row or {}).get(field)) == _text(want.get(field))
            for field in CSV_FIELDS
        )
        if row:
            wrong = [
                f"{field}={row.get(field)!r} (want {want.get(field)!r})"
                for field in CSV_FIELDS
                if _text(row.get(field)) != _text(want.get(field))
            ]
            detail = (f"{rid}: loader row "
                      + ("; ".join(wrong) if wrong else "matches Vettly"))
        else:
            detail = f"{rid}: the loader file carries no row for it"
        ctx.check_l1(f"t0165_{rid}_loader_row_matches_vettly", ok, detail)

    # DEVICE 2 at the third layer: the purge list the warehouse runs after the
    # load. A cycle that read every closed file as an ordinary update hands it
    # an empty list and resurrects sixteen closed files.
    for rid in sorted(retired_ids):
        ctx.check_l1(
            f"t0165_{rid}_is_on_the_retirement_list",
            rid in retired_reported,
            f"{rid} is closed at Vettly and is not on retired_ids "
            f"({len(retired_reported)} entr(y/ies) reported)",
        )
    purged_anything = bool(retired_reported)
    for rid in not_retired_sample:
        ok = purged_anything and rid not in retired_reported
        ctx.check_l1(
            f"t0165_{rid}_is_not_on_the_retirement_list",
            ok,
            f"{rid} is a live record at Vettly and the cycle listed it for "
            "retirement"
            if purged_anything else
            f"{rid}: the cycle listed nothing for retirement at all, so this is "
            "unproven",
        )

    # DEVICE 1: the records the warehouse already holds. Their updated_at sits
    # below the handover cursor, so reporting one is a double-count -- which is
    # exactly what a cycle whose watermark was ignored on the wire does to all
    # of them.
    for control in controls:
        rid = str(control["record_id"])
        kind = str(control["kind"])
        witnessed = bool(reported_expected.get(kind))
        ok = witnessed and rid not in entry_by_id
        ctx.check_l1(
            f"t0165_{rid}_was_already_delivered_and_is_not_reported",
            ok,
            f"{rid} ({kind}) has not moved since the handover cursor and the "
            f"change file reports it as {(entry_by_id.get(rid) or {}).get('op')!r}"
            if witnessed else
            f"{rid} ({kind}): the cycle reported no moved {kind} at all, so "
            "this is unproven",
        )
        ok_csv = witnessed and rid not in loader_by_id
        ctx.check_l1(
            f"t0165_{rid}_was_already_delivered_and_is_not_loaded",
            ok_csv,
            f"{rid} ({kind}) is in the loader file the warehouse will apply"
            if witnessed else
            f"{rid} ({kind}): the cycle reported no moved {kind} at all, so "
            "this is unproven",
        )

    # ------------------------------------------------------- whole cycle ---
    ctx.check_l1(
        "t0165_cycle_resumed_from_the_handover_cursor",
        isinstance(result, dict)
        and _text(result.get("cursor_used")) == _text(key["cursor_s"]),
        f"result.json states cursor_used={(result or {}).get('cursor_used')!r}; "
        f"vaultd delivered through {key['cursor_iso']}, which Vettly's "
        "watermark parameter reads as a different value",
    )
    ctx.check_l1(
        "t0165_next_cursor_is_the_high_water_mark_of_this_cycle",
        isinstance(result, dict)
        and _text(result.get("next_cursor")) == _text(key["next_cursor"]),
        f"result.json states next_cursor={(result or {}).get('next_cursor')!r}; "
        f"the newest record this cycle owns is stamped {key['next_cursor']}",
    )
    ctx.check_l1(
        "t0165_record_count_matches_what_the_tenant_moved",
        isinstance(result, dict)
        and set(result) == RESULT_FIELDS
        and result.get("record_count") == key["record_count"]
        and isinstance(changes, list)
        and len(changes) == len(entry_by_id)
        and all(set(row) == {"record_id", *ENTRY_FIELDS} for row in changes if isinstance(row, dict))
        and isinstance(reported_retired, list)
        and len(reported_retired) == len(retired_reported)
        and len(entry_by_id) == key["record_count"],
        f"result.json states record_count={(result or {}).get('record_count')!r} "
        f"over {len(entry_by_id)} distinct entr(y/ies); Vettly has moved "
        f"{key['record_count']} record(s) since the handover cursor "
        f"(the tenant holds {sum(key['tenant_totals'].values())} in total)",
    )
    reported_counts = (result or {}).get("counts")
    for bucket in ("subject", "check", "report", "upsert", "retire"):
        got = (reported_counts or {}).get(bucket) \
            if isinstance(reported_counts, dict) else None
        ctx.check_l1(
            f"t0165_tally_{bucket}_matches_vendor_truth",
            got == counts[bucket],
            f"result.json tallies {bucket}={got}; Vettly's own state puts "
            f"{counts[bucket]} there",
        )

    disagreements = sorted(
        rid for rid in set(loader_by_id)
        if _text((entry_by_id.get(rid) or {}).get("op"))
        != _text(loader_by_id[rid].get("op"))
        or _text((entry_by_id.get(rid) or {}).get("subject_id"))
        != _text(loader_by_id[rid].get("subject_id"))
    )
    ctx.check_l1(
        "t0165_change_file_and_loader_file_agree_on_every_record",
        bool(loader_by_id)
        and len(loader_rows) == len(loader_by_id) == key["record_count"]
        and all(set(row) == {"record_id", *CSV_FIELDS} for row in loader_rows)
        and not disagreements,
        f"{len(disagreements)} record(s) where the change file and the loader "
        f"file disagree on the operation or the person: {disagreements[:5]}"
        if loader_by_id else "the loader file carries no rows at all",
    )

    # Requirement-shaped, straight off the vendor's own log: the cycle has to
    # have read all three collections. Inaction cannot satisfy this, and
    # neither can a cycle that answered from vaultd's handover file.
    def _served(collection: str) -> int:
        return sum(
            1 for e in request_log
            if str(e.get("path") or "").startswith(f"/v1/{collection}")
            and int(e.get("status") or 0) == 200
        )
    served = {name: _served(name) for name in COLLECTION.values()}
    ctx.check_l1(
        "t0165_cycle_read_all_three_vettly_collections",
        all(count > 0 for count in served.values()),
        f"Vettly served {served['subjects']} subject read(s), "
        f"{served['checks']} check read(s) and {served['reports']} report "
        "read(s); the change file is answerable only from all three",
    )

    data_requests = [
        entry
        for entry in request_log
        if str(entry.get("path") or "").startswith("/v1/")
    ]
    authenticated = bool(data_requests) and all(
        str((entry.get("headers") or {}).get("authorization", "")) == "***"
        for entry in data_requests
    )
    ctx.check_l1(
        "t0165_vettly_data_reads_are_authenticated",
        authenticated,
        f"authenticated={sum(str((entry.get('headers') or {}).get('authorization', '')) == '***' for entry in data_requests)}/{len(data_requests)}",
    )

    credential_query_keys = {
        "access_token", "token", "client_id", "client_secret", "refresh_token"
    }
    leaked = [
        entry
        for entry in data_requests
        if credential_query_keys & set((entry.get("query") or {}).keys())
    ]
    ctx.check_l1(
        "t0165_vettly_credentials_stay_out_of_read_queries",
        bool(data_requests) and not leaked,
        f"data_reads={len(data_requests)} credential_query_reads={len(leaked)}",
    )

    for kind, collection in COLLECTION.items():
        reads = [
            entry
            for entry in request_log
            if entry.get("method") == "GET"
            and entry.get("path") == f"/v1/{collection}"
            and int(entry.get("status") or 0) == 200
        ]
        correctly_narrowed = bool(reads) and all(
            _text((entry.get("query") or {}).get("modified_since"))
            == _text(key["cursor_s"])
            for entry in reads
        )
        ctx.check_l1(
            f"t0165_{kind}_reads_use_the_handover_watermark",
            correctly_narrowed,
            f"{len(reads)} successful list read(s); watermarks="
            f"{[(entry.get('query') or {}).get('modified_since') for entry in reads]}",
        )

    # The five conduct requirements above are unconditional and grade every
    # connector request directly. Invoke the shared rulebook once, excluding
    # those same indices so its traffic-conditional names cannot disappear in
    # starter/stub modes or inflate a do-nothing score.
    await builtin_l2(
        ctx,
        exclude_request_indices=range(len(request_log)),
    )
