"""task-0026 — two_file_join_before_upsert (placemint, zero-lie vendor).

One quarter-close run over the pair of exports, then the same run again.

`input/placement_lines.csv` holds 62 lines under 8 invoice headers in
`input/invoices.csv`. 25 lines are billable, 24 name a placement Placemint has
retired, 3 name an id Placemint never issued, and 10 sit under an invoice that
is `draft`, `void` or missing from the header file altogether. Nothing can be
decided from either file alone: the commission rate is on the header and the
salary is on the line, and whether a placement may be invoiced at all is on
neither.

`docs/` holds Placemint's own documentation, byte-identical to the vendor's
(`LIES.md`: `docs.lies: []`), plus ONE task-local document —
`docs/meridian-quarter-close-runbook.md`, Revenue Ops' internal note, disclaimed
in its third line as "our own note, not Placemint's documentation". The vendor
is honest; the runbook is where this tenant's beliefs live, and two of them are
false.

FOUR independent things have to go right. Each of the two divergences was made
to fail on its own against the live vendor and the consequence measured (rig
probe, this scenario, 269 checks; gold = 269/269, starter and harness stub = 1).

1. **DIVERGENCE A — "the export simply does not contain retired placements any
   more."** The runbook says the per-line existence lookup was removed after the
   Q4 integration went in, because Finance's tool is fed from the same place
   Placemint is. The wire says otherwise: 24 of the 62 lines name a placement
   Placemint has soft-deleted, and `docs/entities.md` says in three separate
   places that a deleted record stays in list responses with `is_deleted: true`
   — discoverable by anyone who looks, invisible to anyone who believes the
   runbook. A believer reports all 24 as `applied` and, because Placemint
   accepts writes to soft-deleted placements by design (`docs/writeback.md`
   § Missing parents), actually invoices them. MEASURED in isolation by
   `variants/second-guess.patch`, which takes `docs/writeback.md` at its word on
   idempotency keys (so device B is repaired) and changes nothing else:
   **192/269 = 0.714**, losing 77 checks.

2. **DIVERGENCE B — "the key is the invoice reference."** The runbook prescribes
   `INV-YYQQ-NN` as the `Idempotency-Key` for the fee update and
   `note-INV-YYQQ-NN` for the note, with a plausible-sounding rationale ("an
   invoice is the unit a client gets billed for"). `docs/writeback.md` says to
   send a unique key with EVERY write and describes the replay semantics
   exactly. Placemint's idempotency store is keyed by the header alone, is
   shared across endpoints, and is consulted BEFORE the target id is resolved,
   so the second and every later write under one invoice replays the FIRST
   write's stored response. The connector is handed `200` and a placement body
   and has no way to notice from the response that its write never happened.
   Five issued invoices carry the 25 billable lines between them; a believer
   lands 5 and silently loses 20. This device is graded on ACCEPTED OUTCOMES —
   the vendor's own state and its notes — never on how many writes were
   attempted, because every attempt "succeeds". MEASURED in isolation by
   `variants/defensive.patch`, which walks the collection and skips retired
   placements (so device A is repaired) and changes nothing else:
   **187/269 = 0.695**, losing 82 checks.

3. THE JOIN ITSELF. A line's fee is `base_salary x fee_pct / 100` and the rate
   exists only on the header; a `withdrawn`/`declined` line earns nothing
   whatever its salary; a line under a `draft`, `void` or absent invoice is held
   and is never looked up, let alone written. Neither file decides anything on
   its own.

4. PARTIAL FAILURE. Three lines name ids Placemint never issued. They must be
   reported `unknown`, not crash the run — and (device B again) a connector that
   discovers unknown ids from the write's own `404` never sees one, because the
   invoice's key was consumed by an earlier line and the replayed `200` arrives
   instead.

MEASURED VARIANTS (all under `variants/`, all run through
`tools/rework/probe/sweep.py --mode naive` with the patch swapped in):

| variant | what it is | score |
|---|---|---|
| gold (`solution.patch`) | one crawl, `is_deleted` respected, per-write keys | **269/269 = 1.000** |
| starter | the shipped skeleton (`NotImplementedError`) | 1/269 = 0.004 |
| harness stub | empty submission | 1/269 = 0.004 |
| `naive.patch` | tidy, faithful to `docs/` INCLUDING the runbook: no lookup, invoice-level keys | **145/269 = 0.539** |
| `variants/defensive.patch` | naive + the guard a cautious engineer adds first: walk the collection and skip retired placements. Fixes A, keeps B | **187/269 = 0.695** |
| `variants/second-guess.patch` | naive + the other obvious guard: `docs/writeback.md` asks for a unique key per write, so use one. Fixes B, keeps A | **192/269 = 0.714** |
| `variants/alt-correct.patch` | legitimate and structurally different: resolves every placement with a GET-by-id instead of a crawl, derives keys as UUIDv5 names, settles the lines in reverse file order and restores file order for the report | **269/269 = 1.000** |

`defensive` and `second-guess` are the two-device proof. Each repairs exactly one
divergence and leaves the other untouched, and each still loses 82 / 77 of the
269 checks — so neither device carries the task alone and neither is a rounding
error. Their failures do not overlap: `defensive` loses the 20 billable
placements whose write was replayed away, `second-guess` loses the 24 retired
ones it invoiced, and by construction the retired set and the applied set are
disjoint. `naive` takes both hits and loses 124.

`alt-correct` is why the starter's transport honours `Retry-After` literally
rather than clamping it: a per-id resolver issues ~124 GETs across two runs and
genuinely crosses Placemint's documented 80/60s budget, where the earlier
20-second clamp produced a real `retry_after_honored` violation on a correct
connector.

WORLD. `docker-compose.yaml` boots the vendor at `CHECKPOINT=56`,
`DATASET_SIZE=400`. Timeline entries 27-56 were appended for this task behind
`mutations.FROZEN_PREFIX_LEN = 26`; checkpoints 0-26 are byte-identical to the
published world at DATASET_SIZE 100/250/400/10000 (108 (size, checkpoint) pairs
diffed against git HEAD), and every appended entry was confirmed to change the
built state and to land in its own collection. The published timeline carried
exactly ONE placement soft-delete, nowhere near enough surface for a per-record
retired device: at ten retired lines `second-guess` measured **0.879**, and the
fix was a wider world (24) rather than more checks about the same ten records.
Twenty-six of the appended entries retire a placement — 24 named by the export
and 2 that are not — so `is_deleted` is not merely a restatement of "appears in
the file". Four more are ordinary stage churn.

EVIDENCE. Every check reads the connector's declared artifact against the answer
key, the vendor's request log, or the vendor's state crawled by this verifier
over its published port — never the connector's account of the vendor. Every
"the connector left X alone" check first proves the connector accept-wrote
something. `builtin_l2` fires once, after the last run, with this verifier's own
request indices excluded.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

from bench.verifier.builtin_l2 import builtin_l2

VENDOR = "placemint"
PAGE = 100
FEE_TOLERANCE = 0.005


# ---------------------------------------------------------------------------
# reading the vendor's own state, over its published port
# ---------------------------------------------------------------------------

def _token(ctx) -> str:
    base = ctx.vendor(VENDOR).base_url
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": ctx.secrets.get("PM_CLIENT_ID", ""),
        "client_secret": ctx.secrets.get("PM_CLIENT_SECRET", ""),
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/oauth/token", data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["access_token"]


def _crawl(ctx, path: str, tok: str, params: dict | None = None) -> list[dict]:
    base = ctx.vendor(VENDOR).base_url
    out: list[dict] = []
    offset = 0
    while True:
        query = dict(params or {})
        query.update({"offset": offset, "limit": PAGE})
        req = urllib.request.Request(
            f"{base}{path}?{urllib.parse.urlencode(query)}",
            headers={"Authorization": f"Bearer {tok}"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            envelope = json.load(resp)
        out.extend(envelope.get("data") or [])
        offset += int(envelope.get("limit") or PAGE)
        if offset >= int(envelope.get("total") or 0):
            return out


def _read_world(ctx, key) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """(placements by id, connector-filed notes grouped by placement_id).

    The note read is narrowed with `modified_since=<notes_watermark>`. Placemint
    stamps every writeback-created record from a counter anchored well past the
    seeded world, and the key records (measured) that ZERO notes sat at or after
    that instant before the connector ran — so this returns exactly the notes
    this vendor lifetime's connector filed, in one page instead of twenty-one.
    """
    tok = _token(ctx)
    placements = {p["id"]: p for p in _crawl(ctx, "/api/placements", tok)}
    filed: dict[str, list[dict]] = {}
    for note in _crawl(ctx, "/api/notes", tok,
                       {"modified_since": key["notes_watermark"]}):
        filed.setdefault(str(note.get("placement_id")), []).append(note)
    return placements, filed


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _read_report(ctx):
    path = Path(ctx.output_dir) / "close_report.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _close(a, b) -> bool:
    fa, fb = _num(a), _num(b)
    return fa is not None and fb is not None and abs(fa - fb) < FEE_TOLERANCE


def _wrote_anything(request_log) -> bool:
    """Did the connector accept-write ANYTHING? The witness every 'left it
    alone' check below is gated on: a submission that never wrote must not bank
    credit for not having written wrongly."""
    return any(
        e.get("method") in ("PATCH", "POST")
        and str(e.get("path", "")).startswith("/api/placements/")
        and int(e.get("status") or 0) in (200, 201)
        for e in request_log
    )


def _observed_placements(request_log, total_before: int) -> set[str]:
    """Placement ids the connector actually LOOKED AT, by either legal route.

    Strategy-agnostic on purpose: a full offset walk of the collection and a
    GET-by-id per named placement are both correct ways to find out whether a
    placement is retired, and the task grades the outcome, not the route.
    """
    seen: set[str] = set()
    covered = 0
    for entry in request_log:
        if entry.get("method") != "GET" or int(entry.get("status") or 0) != 200:
            continue
        path = str(entry.get("path") or "")
        if path == "/api/placements":
            query = entry.get("query") or {}
            try:
                covered = max(covered, int(query.get("offset") or 0)
                              + int(query.get("limit") or PAGE))
            except (TypeError, ValueError):
                continue
        elif path.startswith("/api/placements/") and path.count("/") == 3:
            seen.add(path.rsplit("/", 1)[-1])
    if covered >= total_before:
        return None  # a complete walk: every id was observed
    return seen


def _untouched(placements, baseline, filed_notes, *, wrote_anything):
    """This placement is exactly as the tenant left it, and carries no new note.

    Gated on a witness that the connector wrote SOMETHING: "it did not invoice
    this placement" is free for a submission that invoiced nothing.
    """
    pid = baseline["placement_id"]
    if not wrote_anything:
        return False, f"{pid}: the connector never wrote anything — nothing to judge"
    rec = placements.get(pid)
    if rec is None:
        return False, f"{pid}: the vendor no longer serves this placement"
    problems = []
    for field in ("fee_amount", "stage", "updated_at", "is_deleted"):
        if rec.get(field) != baseline[field]:
            problems.append(f"{field}={rec.get(field)!r} (was {baseline[field]!r})")
    if filed_notes.get(pid):
        problems.append(f"{len(filed_notes[pid])} note(s) filed against it")
    return not problems, f"{pid}: " + (", ".join(problems) or "untouched, no note filed")


def _applied_state(placements, want, *, wrote_anything):
    pid = want["placement_id"]
    if not wrote_anything:
        return False, f"{pid}: the connector never wrote anything"
    rec = placements.get(pid)
    if rec is None:
        return False, f"{pid}: the vendor does not serve this placement"
    problems = []
    if not _close(rec.get("fee_amount"), want["fee_amount"]):
        problems.append(f"fee_amount={rec.get('fee_amount')!r}, want {want['fee_amount']!r}")
    if rec.get("stage") != want["stage"]:
        problems.append(f"stage={rec.get('stage')!r}, want {want['stage']!r}")
    return not problems, f"{pid} ({want['line_ref']}): " + (
        ", ".join(problems) or "carries the closed fee and stage")


def _applied_note(filed_notes, want, author, *, wrote_anything):
    pid = want["placement_id"]
    if not wrote_anything:
        return False, f"{pid}: the connector never wrote anything"
    notes = filed_notes.get(pid) or []
    if len(notes) != 1:
        return False, (
            f"{pid} ({want['line_ref']}): {len(notes)} note(s) filed, expected exactly 1"
            + (f" — bodies {[n.get('body') for n in notes[:3]]}" if notes else "")
        )
    note = notes[0]
    problems = []
    if str(note.get("body")) != want["note_body"]:
        problems.append(f"body={note.get('body')!r}, want {want['note_body']!r}")
    if str(note.get("author")) != author:
        problems.append(f"author={note.get('author')!r}, want {author!r}")
    return not problems, f"{pid} ({want['line_ref']}): " + (
        ", ".join(problems) or "one correct close note")


# ---------------------------------------------------------------------------

async def run(ctx) -> None:
    key = json.loads((Path(ctx.fixtures) / "answer_key.json").read_text(encoding="utf-8"))
    lines = key["lines"]
    baselines = key["baselines"]
    author = key["note_author"]
    applied = [line for line in lines if line["outcome"] == "applied"]

    # -- the close run --------------------------------------------------------
    code, _out, err = ctx.app.run()
    report = _read_report(ctx)
    ctx.check_l1(
        "quarter_close_completed",
        code == 0 and isinstance(report, dict),
        f"exit={code} report={type(report).__name__} stderr={err[:400]}",
    )

    body = report if isinstance(report, dict) else {}
    reported = {
        str(entry.get("line_ref")): entry
        for entry in (body.get("lines") or [])
        if isinstance(entry, dict)
    }

    ctx.check_l1(
        "close_report_accounts_for_every_line",
        body.get("line_count") == key["line_count"]
        and all(body.get(f"{o}_count") == c for o, c in key["counts"].items()),
        "report says line_count={} applied={} held={} retired={} unknown={}; expected {}/{}".format(
            body.get("line_count"), body.get("applied_count"), body.get("held_count"),
            body.get("retired_count"), body.get("unknown_count"),
            key["line_count"], key["counts"],
        ),
    )

    # -- the request log, BEFORE this verifier puts anything in it -------------
    log_after_run = ctx.vendor(VENDOR).request_log()
    n_after_run = len(log_after_run)
    wrote_anything = _wrote_anything(log_after_run)

    observed = _observed_placements(log_after_run, key["placements_total_before"])
    # Only the lines whose outcome the VENDOR decides. A line held on the file
    # evidence alone never needs a lookup, so requiring one would grade a route
    # rather than an outcome; and an id the vendor never issued cannot be
    # observed with a 200 at all.
    named = sorted(set(key["applied_ids"]) | set(key["retired_ids"]))
    if observed is None:
        scan_ok, scan_detail = True, "the connector walked the whole placement collection"
    else:
        missed = [pid for pid in named if pid not in observed]
        scan_ok = not missed
        scan_detail = (
            f"{len(missed)} of {len(named)} named placement(s) were never read before "
            f"being decided, e.g. {missed[:5]}" if missed else
            f"all {len(named)} named placements were read by id"
        )
    ctx.check_l1("close_run_read_every_named_placement", scan_ok, scan_detail)

    # -- per line, at the report layer ----------------------------------------
    # Emitted unconditionally: a run that produced no artifact fails each of
    # these rather than shrinking the denominator.
    for line in lines:
        ref = line["line_ref"]
        got = reported.get(ref)
        where = f"{ref} ({line['placement_id']} on {line['invoice_ref']})"
        ctx.check_l1(
            f"line_{ref}_reported_{line['outcome']}",
            got is not None
            and got.get("invoice_ref") == line["invoice_ref"]
            and got.get("placement_id") == line["placement_id"]
            and got.get("outcome") == line["outcome"],
            f"{where}: report has invoice_ref={(got or {}).get('invoice_ref')!r} "
            f"placement_id={(got or {}).get('placement_id')!r} "
            f"outcome={(got or {}).get('outcome')!r}; expected "
            f"{line['invoice_ref']!r}/{line['placement_id']!r}/{line['outcome']!r}",
        )

    for line in applied:
        ref = line["line_ref"]
        got = reported.get(ref) or {}
        where = f"{ref} ({line['placement_id']})"
        problems = []
        if not _close(got.get("fee_amount"), line["fee_amount"]):
            problems.append(f"fee_amount={got.get('fee_amount')!r}, want {line['fee_amount']!r}")
        if got.get("stage") != line["stage"]:
            problems.append(f"stage={got.get('stage')!r}, want {line['stage']!r}")
        ctx.check_l1(
            f"line_{ref}_reported_fee_and_stage",
            bool(got) and not problems,
            f"{where}: " + (", ".join(problems) or "reported the joined fee and stage"),
        )

    # -- the vendor's own state ------------------------------------------------
    placements, filed_notes = _read_world(ctx, key)
    n_after_probe_1 = len(ctx.vendor(VENDOR).request_log())

    notes_by_id = {
        str(note.get("id")): note
        for notes in filed_notes.values() for note in notes
    }

    for line in applied:
        ctx.check_l1(
            f"placement_{line['placement_id']}_carries_the_closed_fee",
            *_applied_state(placements, line, wrote_anything=wrote_anything),
        )
        ctx.check_l1(
            f"placement_{line['placement_id']}_carries_one_close_note",
            *_applied_note(filed_notes, line, author, wrote_anything=wrote_anything),
        )

        # The report's note_id has to name a note the vendor really holds
        # AGAINST THIS PLACEMENT. A report is free to claim anything; this is
        # the only check that ties its claim to the tenant's state, and it is
        # where a replayed write shows up — the connector was handed a 201 and
        # a note body belonging to a different placement entirely.
        ref = line["line_ref"]
        claimed = str((reported.get(ref) or {}).get("note_id") or "")
        note = notes_by_id.get(claimed)
        if not claimed:
            nid_ok, nid_detail = False, f"{ref}: the report names no note_id"
        elif note is None:
            nid_ok, nid_detail = False, (
                f"{ref}: the report names note {claimed}, which the vendor does not hold"
            )
        else:
            nid_ok = str(note.get("placement_id")) == line["placement_id"]
            nid_detail = (
                f"{ref}: the report names note {claimed}, which the vendor holds against "
                f"{note.get('placement_id')} (expected {line['placement_id']})"
            )
        ctx.check_l1(f"line_{ref}_note_id_is_the_vendors", nid_ok, nid_detail)

    for line in lines:
        if line["outcome"] not in ("retired", "held"):
            continue
        ctx.check_l1(
            f"{line['outcome']}_{line['placement_id']}_was_not_invoiced",
            *_untouched(placements, baselines[line["placement_id"]], filed_notes,
                        wrote_anything=wrote_anything),
        )

    total_filed = sum(len(v) for v in filed_notes.values())
    ctx.check_l1(
        "one_close_note_per_applied_line_and_no_others",
        wrote_anything and total_filed == len(applied),
        f"the tenant holds {total_filed} note(s) filed during this run, expected "
        f"{len(applied)}" if wrote_anything else "the connector never wrote anything",
    )

    live_now = sum(1 for p in placements.values() if not p.get("is_deleted"))
    ctx.check_l1(
        "close_run_retired_nobody_and_revived_nobody",
        wrote_anything and live_now == key["live_placements_before"]
        and len(placements) == key["placements_total_before"],
        f"{len(placements)} placement(s), {live_now} live; expected "
        f"{key['placements_total_before']}/{key['live_placements_before']}"
        if wrote_anything else "the connector never wrote anything",
    )

    # -- the same two files again ---------------------------------------------
    code, _out, err = ctx.app.run()
    rerun_report = _read_report(ctx)
    ctx.check_l1(
        "rerun_completed",
        code == 0 and isinstance(rerun_report, dict),
        f"exit={code} report={type(rerun_report).__name__} stderr={err[:400]}",
    )
    ctx.check_l1(
        "rerun_report_is_unchanged",
        isinstance(rerun_report, dict) and isinstance(report, dict)
        and rerun_report == report,
        "the second run's report differs from the first's"
        if isinstance(rerun_report, dict) else "the second run produced no report",
    )

    n_after_rerun = len(ctx.vendor(VENDOR).request_log())
    placements_again, filed_again = _read_world(ctx, key)
    n_after_probe_2 = len(ctx.vendor(VENDOR).request_log())

    drifted = sorted(
        pid for pid in placements
        if placements_again.get(pid) != placements[pid]
    )
    ctx.check_l1(
        "rerun_left_every_placement_exactly_as_it_was",
        wrote_anything and not drifted,
        f"{len(drifted)} placement(s) changed on the second run, e.g. {drifted[:5]}"
        if wrote_anything else "the connector never wrote anything",
    )
    total_again = sum(len(v) for v in filed_again.values())
    ctx.check_l1(
        "rerun_filed_no_further_notes",
        wrote_anything and total_again == total_filed == len(applied),
        f"the tenant holds {total_again} filed note(s) after the second run "
        f"(had {total_filed}, expected {len(applied)})"
        if wrote_anything else "the connector never wrote anything",
    )

    # Both sides of the ledger again, after the re-run: an over-eager second
    # pass that re-writes a fee is as wrong as one that never wrote it, and a
    # placement the first run correctly left alone can still be invoiced by the
    # second one.
    for line in applied:
        ctx.check_l1(
            f"rerun_placement_{line['placement_id']}_still_closed",
            *_applied_state(placements_again, line, wrote_anything=wrote_anything),
        )
    for line in lines:
        if line["outcome"] not in ("retired", "held"):
            continue
        ctx.check_l1(
            f"rerun_{line['outcome']}_{line['placement_id']}_still_not_invoiced",
            *_untouched(placements_again, baselines[line["placement_id"]], filed_again,
                        wrote_anything=wrote_anything),
        )

    # -- conduct, once per vendor lifetime, over the connector's traffic only --
    await builtin_l2(
        ctx,
        exclude_request_indices=[
            *range(n_after_run, n_after_probe_1),
            *range(n_after_rerun, n_after_probe_2),
        ],
        app_runs=2,
    )
