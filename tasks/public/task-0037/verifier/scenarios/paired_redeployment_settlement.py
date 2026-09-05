"""task-0037 — non_atomic_paired_writeback (placemint, zero-lie vendor, `fix`).

One redeployment run over the ATS's nightly export, then the same run again.

`input/redeployments.csv` holds 134 movements, each naming TWO placements: the
one the candidate left and the one they joined. Meridian's rule is that both
ends land or neither does. Placemint offers no way to do that — `docs/writeback.md`
describes two independent write endpoints, an `Idempotency-Key` that replays a
stored result, and no batch, no envelope, no rollback and no note delete — but
`docs/meridian-redeployment-runbook.md`, Delivery Ops' own note, disclaimed in
its second line as "our own note, not Placemint's documentation. Last touched the
spring before last", asserts the opposite in as many words:

    Placemint takes the pair as one movement ... if the joiner is refused, the
    leaver goes back with it and there is nothing for us to undo.

That belief is the whole of the shipped design, and it is why the connector
writes the leaver first and simply stops the row on a refusal.

THE MATCHED PAIR. Reversing the order is the obvious fix and it moves the damage
rather than removing it. Measured on the export `gen_answer_key_0184.py` writes,
which it refuses to emit unless the two sides are the same size:

| group | rows | shipped (leaver first) | reversed (joiner first) |
|---|---|---|---|
| `applied` | 26 | lands in full | lands in full |
| `bad_from_status` | 20 | leaver's PATCH 422s first — clean | joiner already moved  <- starter-favour |
| `unknown_from` | 14 | leaver's PATCH 404s first — clean | joiner already moved  <- starter-favour |
| `bad_to_status` | 20 | leaver already moved  <- naive-favour | joiner's PATCH 422s first — clean |
| `unknown_to` | 14 | leaver already moved  <- naive-favour | joiner's PATCH 404s first — clean |
| `stale_from` | 40 | **both ends move** | **both ends move** |

`stale_from` is the group that defeats both, and any amount of local validation
with them: the leaver has already been closed by the desk (`placed` or
`fell_through`), the export is a day behind and does not know it, and BOTH
`PATCH`es succeed, so nothing bounces and the log calls the row applied. The
only way to see it is to ask Placemint what stage the leaver is in — which is
also the only way to know whether either id resolves at all. Three of the five
rules are facts about the vendor, not about the file, so the decision has to be
taken before the first write, against a snapshot of the placement collection.

MEASURED (rig probe, this scenario, 307 checks):

| variant | what it is | score |
|---|---|---|
| gold (`solution.patch`) | crawl, decide, then write | **307/307 = 1.000** |
| starter | the shipped connector: leaver first | 188/307 = 0.612 |
| harness stub | empty submission | 1/307 = 0.003 |
| `naive.patch` | the obvious fix: joiner first | **188/307 = 0.612** |
| `variants/defensive.patch` | joiner first + local status validation | 208/307 = 0.678 |
| `variants/second-guess.patch` | leaver first + local status validation | 208/307 = 0.678 |
| `variants/alt-correct.patch` | crawl and decide, by a different route | 307/307 = 1.000 |

Starter and naive differ on 68 checks — **34 starter-favour** (`bad_from_status`
20 + `unknown_from` 14, where the reversed connector moves a joiner it should not
have touched) and **34 naive-favour** (`bad_to_status` 20 + `unknown_to` 14,
where the shipped connector moves a leaver it should not have touched). Neither
write order can pass both. Each fails 119 checks in total, and 80 of those — the
40 `stale_from` rows, twice over — are the same 80 for both of them: the group
neither order nor any local guard can reach. That is what keeps the two
one-guard basins (`defensive`, `second-guess`) at 0.678 rather than at 1.000.

EVIDENCE. Every check reads the answer key or the vendor's own state crawled by
this verifier over its published port — never the connector's account of it. The
note read is narrowed with `modified_since=<notes_watermark>`, an instant the key
records (and asserts, against the crawled world) as holding zero seeded notes, so
it returns exactly what this run filed. Every "nothing was written here" check
first proves the run got a write ACCEPTED — a refused attempt is not evidence of
restraint. `builtin_l2` fires once, after the last run, with this verifier's own
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
    placements = {str(p["id"]): p for p in _crawl(ctx, "/api/placements", tok)}
    filed: dict[str, list[dict]] = {}
    for note in _crawl(ctx, "/api/notes", tok, {"modified_since": key["notes_watermark"]}):
        filed.setdefault(str(note.get("placement_id")), []).append(note)
    return placements, filed


def _read_log(ctx):
    path = Path(ctx.output_dir) / "movement_log.json"
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


def _accept_wrote(request_log) -> bool:
    """Did the submission get a write ACCEPTED? Attempts do not count."""
    return any(
        e.get("method") in ("PATCH", "POST")
        and str(e.get("path", "")).startswith("/api/placements/")
        and int(e.get("status") or 0) in (200, 201)
        for e in request_log
    )


def _both_ends_landed(placements, want, *, wrote):
    ref = want["movement_ref"]
    if not wrote:
        return False, f"{ref}: the connector never got a write accepted"
    problems = []
    leaver = placements.get(want["from_placement_id"])
    joiner = placements.get(want["to_placement_id"])
    if leaver is None:
        problems.append(f"the vendor does not serve leaver {want['from_placement_id']}")
    elif str(leaver.get("stage")) != want["target_from_stage"]:
        problems.append(
            f"leaver stage={leaver.get('stage')!r}, want {want['target_from_stage']!r}")
    if joiner is None:
        problems.append(f"the vendor does not serve joiner {want['to_placement_id']}")
    else:
        if str(joiner.get("stage")) != want["target_to_stage"]:
            problems.append(
                f"joiner stage={joiner.get('stage')!r}, want {want['target_to_stage']!r}")
        if not _close(joiner.get("fee_amount"), want["target_to_fee"]):
            problems.append(
                f"joiner fee_amount={joiner.get('fee_amount')!r}, want {want['target_to_fee']!r}")
    return not problems, f"{ref} ({want['group']}): " + (
        ", ".join(problems[:3]) or "both ends carry what the export asked for")


def _note_is_the_desks_words(filed, entry, want, author, *, wrote):
    ref = want["movement_ref"]
    if not wrote:
        return False, f"{ref}: the connector never got a write accepted"
    mine = [n for n in filed.get(want["to_placement_id"], [])
            if str(n.get("author")) == author]
    if len(mine) != 1:
        return False, (
            f"{ref}: {len(mine)} note(s) from {author} on joiner "
            f"{want['to_placement_id']}, expected exactly 1")
    note = mine[0]
    problems = []
    if str(note.get("body")) != want["note_body"]:
        problems.append(
            f"body is {len(str(note.get('body')))} char(s) {str(note.get('body'))[-28:]!r}, "
            f"want {len(want['note_body'])} char(s) ending {want['note_body'][-28:]!r}")
    claimed = str((entry or {}).get("note_id") or "")
    if claimed != str(note.get("id")):
        problems.append(
            f"the log names note_id={claimed!r}, the vendor holds {note.get('id')!r}")
    return not problems, f"{ref} ({want['group']}): " + (
        ", ".join(problems) or "the desk's words, verbatim, on the joiner")


def _left_no_trace(placements, filed, entry, want, baselines, author, *, wrote):
    """Neither end of this pair carries anything this run put there.

    Gated on a witness that the run got a write ACCEPTED somewhere, because
    "it wrote nothing here" is free for a run that wrote nothing anywhere.
    """
    ref = want["movement_ref"]
    if not wrote:
        return False, f"{ref}: the connector never got a write accepted"
    problems = []
    for role, pid in (("leaver", want["from_placement_id"]),
                      ("joiner", want["to_placement_id"])):
        base = baselines.get(pid)
        if base is None:
            # An id the vendor never issued: it must still be serving nothing.
            if pid in placements:
                problems.append(f"the vendor now serves {role} {pid}, which it never issued")
            continue
        record = placements.get(pid)
        if record is None:
            problems.append(f"the vendor no longer serves {role} {pid}")
            continue
        if str(record.get("stage")) != str(base["stage"]):
            problems.append(
                f"{role} {pid} stage moved {base['stage']!r} -> {record.get('stage')!r}")
        if not _close(record.get("fee_amount"), base["fee_amount"]) and not (
                record.get("fee_amount") is None and base["fee_amount"] is None):
            problems.append(
                f"{role} {pid} fee moved {base['fee_amount']!r} -> {record.get('fee_amount')!r}")
        mine = [n for n in filed.get(pid, []) if str(n.get("author")) == author]
        if mine:
            problems.append(f"{len(mine)} note(s) from {author} filed on {role} {pid}")
    if (entry or {}).get("note_id") is not None:
        problems.append(f"the log names note_id={(entry or {}).get('note_id')!r}")
    return not problems, f"{ref} ({want['group']}): " + (
        ", ".join(problems[:3]) or "neither end was touched")


async def run(ctx) -> None:
    key = json.loads((Path(ctx.fixtures) / "answer_key.json").read_text(encoding="utf-8"))
    rows = key["rows"]
    applied = [r for r in rows if r["outcome"] == "applied"]
    unapplied = [r for r in rows if r["outcome"] != "applied"]
    baselines = key["baselines"]
    author = key["note_author"]

    code, _out, err = ctx.app.run()
    log = _read_log(ctx)
    ctx.check_l1(
        "redeployment_run_completed",
        code == 0 and isinstance(log, dict),
        f"exit={code} log={type(log).__name__} stderr={err[:400]}",
    )

    body = log if isinstance(log, dict) else {}
    entries = [e for e in (body.get("movements") or []) if isinstance(e, dict)]
    by_ref: dict[str, list[dict]] = {}
    for entry in entries:
        by_ref.setdefault(str(entry.get("movement_ref")), []).append(entry)

    ctx.check_l1(
        "movement_log_accounts_for_every_row_once",
        body.get("row_count") == key["row_count"]
        and body.get("applied_count") == key["outcome_counts"]["applied"]
        and body.get("rejected_count") == key["outcome_counts"]["rejected"]
        and body.get("unknown_count") == key["outcome_counts"]["unknown"],
        "log says row_count={} applied={} rejected={} unknown={}; the export holds {} "
        "row(s), {} of them applicable, {} the rules exclude and {} naming an id the "
        "vendor never issued".format(
            body.get("row_count"), body.get("applied_count"), body.get("rejected_count"),
            body.get("unknown_count"), key["row_count"],
            key["outcome_counts"]["applied"], key["outcome_counts"]["rejected"],
            key["outcome_counts"]["unknown"]),
    )

    # Emitted unconditionally, one per row the export holds: a run that produced
    # no artifact fails each of these rather than shrinking the denominator.
    for want in rows:
        ref = want["movement_ref"]
        got = by_ref.get(ref) or []
        entry = got[0] if len(got) == 1 else {}
        expected_keys = {
            "movement_ref", "from_placement_id", "to_placement_id", "outcome",
            "from_stage", "to_stage", "to_fee_amount", "note_id", "source_line",
        }
        applied_shape = (
            entry.get("from_stage") == want["target_from_stage"]
            and entry.get("to_stage") == want["target_to_stage"]
            and _close(entry.get("to_fee_amount"), want["target_to_fee"])
            and isinstance(entry.get("note_id"), str)
            and bool(entry.get("note_id"))
        ) if want["outcome"] == "applied" else (
            entry.get("from_stage") is None
            and entry.get("to_stage") is None
            and entry.get("to_fee_amount") is None
            and entry.get("note_id") is None
        )
        ok = (
            len(got) == 1 and set(entry) == expected_keys
            and entry.get("movement_ref") == ref
            and entry.get("from_placement_id") == want["from_placement_id"]
            and entry.get("to_placement_id") == want["to_placement_id"]
            and entry.get("outcome") == want["outcome"]
            and entry.get("source_line") == want["source_line"]
            and applied_shape
        )
        ctx.check_l1(
            f"redeployment_{ref}_logged_{want['outcome']}",
            ok,
            f"{ref} ({want['group']}): {len(got)} log entr(ies), outcome(s) "
            f"{[e.get('outcome') for e in got]}, expected exactly one {want['outcome']!r}",
        )

    log_after_run = ctx.vendor(VENDOR).request_log()
    n_after_run = len(log_after_run)
    wrote = _accept_wrote(log_after_run)

    placements, filed = _read_world(ctx, key)
    n_after_probe_1 = len(ctx.vendor(VENDOR).request_log())

    for want in applied:
        ref = want["movement_ref"]
        entry = (by_ref.get(ref) or [{}])[0]
        ctx.check_l1(
            f"redeployment_{ref}_landed_on_both_ends",
            *_both_ends_landed(placements, want, wrote=wrote),
        )
        ctx.check_l1(
            f"redeployment_{ref}_note_is_the_desks_words",
            *_note_is_the_desks_words(filed, entry, want, author, wrote=wrote),
        )

    for want in unapplied:
        ref = want["movement_ref"]
        entry = (by_ref.get(ref) or [{}])[0]
        ctx.check_l1(
            f"excluded_{ref}_left_neither_end_touched",
            *_left_no_trace(placements, filed, entry, want, baselines, author, wrote=wrote),
        )

    total_filed = sum(
        1 for group in filed.values() for n in group if str(n.get("author")) == author)
    ctx.check_l1(
        "one_note_filed_per_applied_redeployment_and_no_more",
        wrote and total_filed == len(applied),
        f"the tenant holds {total_filed} note(s) from {author}, expected {len(applied)}"
        if wrote else "the connector never got a write accepted",
    )

    moved = sorted(
        pid for pid, base in baselines.items()
        if pid in placements and str(placements[pid].get("stage")) != str(base["stage"]))
    should_move = {r["from_placement_id"] for r in applied} | {
        r["to_placement_id"] for r in applied}
    stray = sorted(set(moved) - should_move)
    ctx.check_l1(
        "no_placement_moved_that_the_rules_exclude",
        wrote and not stray,
        f"{len(stray)} placement(s) the rules exclude were moved anyway, e.g. {stray[:5]}"
        if wrote else "the connector never got a write accepted",
    )

    # -- the same export again -------------------------------------------------
    code, _out, err = ctx.app.run()
    rerun_log = _read_log(ctx)
    ctx.check_l1(
        "rerun_completed",
        code == 0 and isinstance(rerun_log, dict),
        f"exit={code} log={type(rerun_log).__name__} stderr={err[:400]}",
    )
    # NOT "the log is byte-identical": a connector that decides against the
    # vendor's state necessarily sees a different world on the second pass --
    # the leavers it closed are closed now, so rows it applied are rows the
    # desk has already dealt with. What has to hold is that the second log
    # still accounts for the export exactly once, and that Placemint did not
    # move (checked below, against the vendor).
    rerun_refs = [str(e.get("movement_ref"))
                  for e in ((rerun_log or {}).get("movements") or [])
                  if isinstance(e, dict)]
    ctx.check_l1(
        "rerun_log_still_accounts_for_every_row_once",
        (rerun_log or {}).get("row_count") == key["row_count"]
        and sorted(rerun_refs) == sorted(r["movement_ref"] for r in rows),
        f"the second run logged {len(rerun_refs)} entr(ies) over "
        f"{len(set(rerun_refs))} distinct ref(s); the export holds {key['row_count']}",
    )

    n_after_rerun = len(ctx.vendor(VENDOR).request_log())
    placements_again, filed_again = _read_world(ctx, key)
    n_after_probe_2 = len(ctx.vendor(VENDOR).request_log())

    drifted = sorted(pid for pid in placements if placements_again.get(pid) != placements[pid])
    ctx.check_l1(
        "rerun_left_every_placement_exactly_as_it_was",
        wrote and not drifted,
        f"{len(drifted)} placement(s) changed on the second run, e.g. {drifted[:5]}"
        if wrote else "the connector never got a write accepted",
    )
    total_again = sum(
        1 for group in filed_again.values() for n in group if str(n.get("author")) == author)
    ctx.check_l1(
        "rerun_filed_no_further_notes",
        wrote and total_again == total_filed == len(applied),
        f"the tenant holds {total_again} note(s) from {author} after the second run "
        f"(had {total_filed}, expected {len(applied)})"
        if wrote else "the connector never got a write accepted",
    )

    await builtin_l2(
        ctx,
        exclude_request_indices=[
            *range(n_after_run, n_after_probe_1),
            *range(n_after_rerun, n_after_probe_2),
        ],
        app_runs=2,
    )
