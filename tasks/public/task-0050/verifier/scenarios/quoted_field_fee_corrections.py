"""task-0050 — quoted_delimiter_csv_corruption (placemint, zero-lie vendor, `fix`).

One corrections run over Finance's export, then the same run again.

`input/corrections.csv` is a genuine RFC-4180 file holding 42 corrections across
64 physical lines. The shipped connector reads it a LINE at a time and splits on
commas, which is what `docs/meridian-fee-corrections-runbook.md` — Revenue Ops'
own note, disclaimed in its third line as "our own note, not Placemint's
documentation. Last touched two Novembers ago" — says the export permits:
"Nothing in it is ever quoted... no CSV library, no dependency, no surprises."
Finance changed reporting tools. The wire (the file) now quotes any field that
carries a comma and lets free text span lines.

THE DEVICE. A physical line is not a record, and the export is built so that the
two wrong readers fail differently — measured on the bytes shipped, by
`gen_answer_key_0183.py`, which refuses to write a file whose lines do not
classify exactly this way:

| group | rows | a line-splitter (shipped) | a per-LINE `csv.reader` (the usual fix) |
|---|---|---|---|
| `plain` | 8 | accepts, correct | accepts, correct |
| `quoted` | 10 | rejects (comma inside a quoted field) | accepts, correct |
| `nl_tail` | 8 | rejects | **ACCEPTS THE HEAD FRAGMENT** and writes the client's reason truncated mid-sentence |
| `nl_mid` | 14 | rejects | rejects |
| `unknown` | 2 | accepts, 404 | accepts, 404 |

That is the whole point of the `nl_tail` group and it is where the matched pair
lives: the SHIPPED connector, wrong as it is, obeys its own runbook rule ("a
half-written correction applied to a live placement is much worse than one we
did not apply") and writes nothing for those rows, while the per-line fix
half-applies them. So `placement_*_free_of_half_written_values` is a family the
STARTER passes and the NAIVE fails, and `correction_*_landed_on_the_placement`
is a family the NAIVE passes and the STARTER fails. Neither reader can pass both
without parsing the file the way it is actually written — driven by the FILE
OBJECT, which is the only thing that can see a record spanning lines.

MEASURED (rig probe, this scenario, 209 checks):

| variant | what it is | score |
|---|---|---|
| gold (`solution.patch`) | `csv.reader` over the file object | **209/209 = 1.000** |
| starter | the shipped connector: 8 applied, 54 "rejected", 64 "rows" | 83/209 = 0.397 |
| harness stub | empty submission | 2/209 = 0.010 |
| `naive.patch` | per-line `csv.reader`: 26 applied, 36 rejected, 64 "rows" | **139/209 = 0.665** |

Starter/naive differ on 72 checks: **8 starter-favour** (the `nl_tail`
placements the naive half-writes) and **64 naive-favour** (the `quoted` and
`nl_tail` corrections it lands that the starter never attempts).

EVIDENCE. Every check reads the connector's declared artifact against the answer
key or the vendor's state crawled by this verifier over its published port —
never the connector's account of the vendor. The note read is narrowed with
`modified_since=<notes_watermark>`, an instant the key records (measured) as
holding zero seeded notes, so it returns exactly what this run filed. Every
"nothing half-written landed here" check first proves the connector accept-wrote
something. `builtin_l2` fires once, after the last run, with this verifier's own
request indices excluded.
"""

from __future__ import annotations

import csv
import json
import urllib.parse
import urllib.request
from pathlib import Path

from bench.verifier.builtin_l2 import builtin_l2

VENDOR = "placemint"
PAGE = 100
FEE_TOLERANCE = 0.005


def _token(ctx) -> str:
    base = ctx.vendor(VENDOR).base_url
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": ctx.secrets.get("PM_CLIENT_ID", ""),
        "client_secret": ctx.secrets.get("PM_CLIENT_SECRET", ""),
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/oauth/token", data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
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
            headers={"Authorization": f"Bearer {tok}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            envelope = json.load(resp)
        out.extend(envelope.get("data") or [])
        offset += int(envelope.get("limit") or PAGE)
        if offset >= int(envelope.get("total") or 0):
            return out


def _read_world(ctx, key):
    tok = _token(ctx)
    placements = {p["id"]: p for p in _crawl(ctx, "/api/placements", tok)}
    filed: dict[str, list[dict]] = {}
    for note in _crawl(ctx, "/api/notes", tok, {"modified_since": key["notes_watermark"]}):
        filed.setdefault(str(note.get("placement_id")), []).append(note)
    return placements, filed


def _read_log(ctx):
    path = Path(ctx.output_dir) / "correction_log.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _source_lines() -> dict[str, int]:
    path = Path(__file__).parents[2] / "repo" / "input" / "corrections.csv"
    out: dict[str, int] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        while True:
            source_line = reader.line_num + 1
            try:
                row = next(reader)
            except StopIteration:
                return out
            if row:
                out[row[0]] = source_line


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _close(a, b) -> bool:
    fa, fb = _num(a), _num(b)
    return fa is not None and fb is not None and abs(fa - fb) < FEE_TOLERANCE


def _accept_wrote(request_log) -> bool:
    return any(
        e.get("method") in ("PATCH", "POST")
        and str(e.get("path", "")).startswith("/api/placements/")
        and int(e.get("status") or 0) in (200, 201)
        for e in request_log
    )


def _landed(placements, want, *, wrote):
    pid = want["placement_id"]
    if not wrote:
        return False, f"{pid}: the connector never wrote anything"
    rec = placements.get(pid)
    if rec is None:
        return False, f"{pid}: the vendor does not serve this placement"
    problems = []
    if str(rec.get("role_title")) != want["role_title"]:
        problems.append(f"role_title={rec.get('role_title')!r}, want {want['role_title']!r}")
    if not _close(rec.get("fee_amount"), want["fee_amount"]):
        problems.append(f"fee_amount={rec.get('fee_amount')!r}, want {want['fee_amount']!r}")
    return not problems, f"{want['correction_ref']} ({pid}, {want['group']}): " + (
        ", ".join(problems) or "carries the corrected role and fee")


def _note_verbatim(filed, want, author, *, wrote):
    pid = want["placement_id"]
    if not wrote:
        return False, f"{pid}: the connector never wrote anything"
    notes = filed.get(pid) or []
    if len(notes) != 1:
        return False, (
            f"{want['correction_ref']} ({pid}, {want['group']}): {len(notes)} note(s) "
            f"filed, expected exactly 1")
    note = notes[0]
    body = str(note.get("body"))
    problems = []
    if body != want["note_body"]:
        problems.append(
            f"body is {len(body)} char(s) {body[-28:]!r}, want {len(want['note_body'])} "
            f"char(s) ending {want['note_body'][-28:]!r}")
    if str(note.get("author")) != author:
        problems.append(f"author={note.get('author')!r}, want {author!r}")
    return not problems, f"{want['correction_ref']} ({pid}, {want['group']}): " + (
        ", ".join(problems) or "the client's words, verbatim")


def _no_half_written(placements, filed, want, baseline, *, wrote):
    """Nothing on this placement is a value that is in NEITHER the export nor
    the placement's own history.

    This is the family the shipped connector passes and a per-line fix does not:
    refusing a row you cannot read whole is the runbook's rule and it is correct;
    writing the readable half of it is not. Gated on a witness that the run
    accept-wrote something, because "it wrote nothing wrong" is free for a run
    that wrote nothing.
    """
    pid = want["placement_id"]
    if not wrote:
        return False, f"{pid}: the connector never wrote anything"
    rec = placements.get(pid)
    if rec is None:
        return False, f"{pid}: the vendor does not serve this placement"
    problems = []
    role = str(rec.get("role_title"))
    if role not in (want["role_title"], baseline["role_title"]):
        problems.append(f"role_title={role!r} is neither the correction nor the original")
    if not (_close(rec.get("fee_amount"), want["fee_amount"])
            or _close(rec.get("fee_amount"), baseline["fee_amount"])):
        problems.append(
            f"fee_amount={rec.get('fee_amount')!r} is neither the correction nor the original")
    for note in filed.get(pid) or []:
        body = str(note.get("body"))
        if body != want["note_body"]:
            problems.append(
                f"a filed note holds {len(body)} char(s) ending {body[-28:]!r}, which is not "
                f"the {len(want['note_body'])}-char reason the export carries")
    return not problems, f"{want['correction_ref']} ({pid}, {want['group']}): " + (
        ", ".join(problems[:3]) or "nothing half-written landed here")


async def run(ctx) -> None:
    key = json.loads((Path(ctx.fixtures) / "answer_key.json").read_text(encoding="utf-8"))
    rows = key["rows"]
    applied = [r for r in rows if r["outcome"] == "applied"]
    baselines = key["baselines"]
    author = key["note_author"]

    code, _out, err = ctx.app.run()
    log = _read_log(ctx)
    ctx.check_l1(
        "corrections_run_completed",
        code == 0 and isinstance(log, dict),
        f"exit={code} log={type(log).__name__} stderr={err[:400]}",
    )

    body = log if isinstance(log, dict) else {}
    entries = [e for e in (body.get("corrections") or []) if isinstance(e, dict)]
    by_ref: dict[str, list[dict]] = {}
    for entry in entries:
        by_ref.setdefault(str(entry.get("correction_ref")), []).append(entry)

    source_lines = _source_lines()
    exact_log = (
        set(body) == {
            "row_count", "applied_count", "rejected_count", "unknown_count", "corrections"
        }
        and len(entries) == key["row_count"]
        and all(set(entry) == {
            "correction_ref", "placement_id", "outcome", "role_title", "fee_amount",
            "note_id", "source_line",
        } for entry in entries)
    )
    for want in rows:
        got = by_ref.get(want["correction_ref"], [])
        if len(got) != 1:
            exact_log = False
            continue
        entry = got[0]
        exact_log = exact_log and (
            entry.get("placement_id") == want["placement_id"]
            and entry.get("outcome") == want["outcome"]
            and entry.get("source_line") == source_lines.get(want["correction_ref"])
        )
        if want["outcome"] == "applied":
            exact_log = exact_log and (
                entry.get("role_title") == want["role_title"]
                and _close(entry.get("fee_amount"), want["fee_amount"])
                and bool(entry.get("note_id"))
            )
        else:
            exact_log = exact_log and all(
                entry.get(field) is None for field in ("role_title", "fee_amount", "note_id")
            )
    ctx.check_l1(
        "correction_log_exact_artifact",
        exact_log,
        "correction_log.json must have the documented schema and exact per-correction "
        "identity, outcome, values, and logical CSV source line",
    )

    ctx.check_l1(
        "correction_log_accounts_for_every_row_once",
        body.get("row_count") == key["row_count"]
        and body.get("applied_count") == key["counts"]["applied"]
        and body.get("unknown_count") == key["counts"]["unknown"]
        and body.get("rejected_count") == 0,
        "log says row_count={} applied={} rejected={} unknown={}; the export holds {} "
        "correction(s), {} of them applicable and {} naming an id the vendor never "
        "issued, and none of them unreadable".format(
            body.get("row_count"), body.get("applied_count"), body.get("rejected_count"),
            body.get("unknown_count"), key["row_count"], key["counts"]["applied"],
            key["counts"]["unknown"]),
    )

    # Emitted unconditionally, one per correction the export actually holds: a
    # run that produced no artifact fails each of these rather than shrinking
    # the denominator, and a run that logged a fragment as an extra "row" cannot
    # earn credit for it.
    for want in rows:
        ref = want["correction_ref"]
        got = by_ref.get(ref) or []
        ok = len(got) == 1 and got[0].get("outcome") == want["outcome"]
        ctx.check_l1(
            f"correction_{ref}_logged_{want['outcome']}",
            ok,
            f"{ref} ({want['placement_id']}, {want['group']}): {len(got)} log entr(ies), "
            f"outcome(s) {[e.get('outcome') for e in got]}, expected exactly one "
            f"{want['outcome']!r}",
        )

    log_after_run = ctx.vendor(VENDOR).request_log()
    n_after_run = len(log_after_run)
    wrote = _accept_wrote(log_after_run)

    placements, filed = _read_world(ctx, key)
    n_after_probe_1 = len(ctx.vendor(VENDOR).request_log())
    notes_by_id = {str(n.get("id")): n for group in filed.values() for n in group}

    for want in applied:
        ref = want["correction_ref"]
        ctx.check_l1(
            f"correction_{ref}_landed_on_the_placement",
            *_landed(placements, want, wrote=wrote),
        )
        ctx.check_l1(
            f"correction_{ref}_note_is_the_clients_words",
            *_note_verbatim(filed, want, author, wrote=wrote),
        )
        ctx.check_l1(
            f"placement_{want['placement_id']}_free_of_half_written_values",
            *_no_half_written(placements, filed, want, baselines[want["placement_id"]],
                              wrote=wrote),
        )

        # The log's note_id has to name a note the vendor really holds against
        # THIS placement. A log is free to claim anything.
        claimed = str(((by_ref.get(ref) or [{}])[0]).get("note_id") or "")
        note = notes_by_id.get(claimed)
        if not claimed:
            nid_ok, nid_detail = False, f"{ref}: the log names no note_id"
        elif note is None:
            nid_ok, nid_detail = False, (
                f"{ref}: the log names note {claimed}, which the vendor does not hold")
        else:
            nid_ok = str(note.get("placement_id")) == want["placement_id"]
            nid_detail = (
                f"{ref}: the log names note {claimed}, which the vendor holds against "
                f"{note.get('placement_id')} (expected {want['placement_id']})")
        ctx.check_l1(f"correction_{ref}_log_note_id_is_the_vendors", nid_ok, nid_detail)

    total_filed = sum(len(v) for v in filed.values())
    ctx.check_l1(
        "one_note_filed_per_applicable_correction",
        wrote and total_filed == len(applied),
        f"the tenant holds {total_filed} note(s) filed during this run, expected "
        f"{len(applied)}" if wrote else "the connector never wrote anything",
    )

    # -- the same export again -------------------------------------------------
    code, _out, err = ctx.app.run()
    rerun_log = _read_log(ctx)
    ctx.check_l1(
        "rerun_completed",
        code == 0 and isinstance(rerun_log, dict),
        f"exit={code} log={type(rerun_log).__name__} stderr={err[:400]}",
    )
    ctx.check_l1(
        "rerun_log_is_unchanged",
        isinstance(rerun_log, dict) and isinstance(log, dict) and rerun_log == log,
        "the second run's log differs from the first's"
        if isinstance(rerun_log, dict) else "the second run produced no log",
    )

    n_after_rerun = len(ctx.vendor(VENDOR).request_log())
    placements_again, filed_again = _read_world(ctx, key)
    n_after_probe_2 = len(ctx.vendor(VENDOR).request_log())

    drifted = sorted(pid for pid in placements if placements_again.get(pid) != placements[pid])
    ctx.check_l1(
        "rerun_left_every_placement_exactly_as_it_was",
        wrote and not drifted,
        f"{len(drifted)} placement(s) changed on the second run, e.g. {drifted[:5]}"
        if wrote else "the connector never wrote anything",
    )
    total_again = sum(len(v) for v in filed_again.values())
    ctx.check_l1(
        "rerun_filed_no_further_notes",
        wrote and total_again == total_filed == len(applied),
        f"the tenant holds {total_again} filed note(s) after the second run "
        f"(had {total_filed}, expected {len(applied)})"
        if wrote else "the connector never wrote anything",
    )
    for want in applied:
        ctx.check_l1(
            f"rerun_correction_{want['correction_ref']}_still_landed",
            *_landed(placements_again, want, wrote=wrote),
        )

    await builtin_l2(
        ctx,
        exclude_request_indices=[
            *range(n_after_run, n_after_probe_1),
            *range(n_after_rerun, n_after_probe_2),
        ],
        app_runs=2,
    )
