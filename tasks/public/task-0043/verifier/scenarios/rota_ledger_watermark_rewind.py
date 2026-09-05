"""task-0043 — dst_fallback_double_count.

Three consecutive nightly passes of Marchfield's rota sync, each against a
Rosterly booted one checkpoint further along (20 -> 34 -> 55), with the
connector's watermark carried between them in `/app/state`.

This is a `fix`: the shipped connector works. It authenticates, pages every
collection with `modified_since`, normalises both of Rosterly's timestamp wire
formats correctly, writes the ledger and the pass summary, and exits 0. Payroll
reporting is nonsense anyway, because it counts the same night more than once.

THE DIVERGENCE. Rosterly is a zero-lie vendor (`LIES.md`: `docs.lies: []`) and
`docs/pagination.md` is explicit that `modified_since` returns records "whose
`updated_at` is at or after the watermark" and that "the server clock has no
skew — timestamps you read are the timestamps you can trust for your next
watermark". What is wrong is Marchfield's own note,
`docs/marchfield-rota-sync-runbook.md`, attributed and dated in its third line
("Data Platform. This is our own note, not Rosterly's documentation. Last
touched in November, after the incident below"):

> The pass hands Rosterly the newest `updated_at` it saw and then **takes an
> hour back off it** [...] Marta spent the Monday on it and concluded that the
> hour that repeats on the fall-back had let the watermark run ahead [...]
> **Do not take the rewind out to make a duplicate go away** — the duplicate is
> the symptom the rewind exists to prevent.

The diagnosis is backwards, and the note talks the reader out of the fix. The
whole change timeline is 55 minutes wide (measured: first change
2026-07-23T14:03:52Z, last 2026-07-23T14:55:02Z), so an hour of rewind reaches
back past EVERY change already in the ledger. `gen_answer_key_0171.py` asserts
that property against the live vendor and refuses to write a key if it stops
holding.

Its second false claim is aimed at the other half:

> Rosterly takes a record out of the feed once it is gone [...] which is why the
> pass writes every row it receives as an upsert.

`docs/index.md` §Entities says the opposite in two lines — "deleted records are
**not removed** from list responses; they carry `is_deleted: true`. Always check
this flag" — and the wire agrees: 10 of the 55 changes across the three passes
are soft deletes, and the shipped connector files every one of them as an
upsert, so the loader never tombstones anybody. The `removed` list in every pass
summary comes out empty, which the runbook itself records as "always empty and
nobody has exercised it".

MEASURED WORLD (all three passes, from the live vendor):

    pass 1  CHECKPOINT=20   20 changes   19 upserts   1 delete
    pass 2  CHECKPOINT=34   14 changes   10 upserts   4 deletes
    pass 3  CHECKPOINT=55   21 changes   16 upserts   5 deletes
                            55 changes   45 upserts  10 deletes

The shipped connector ledgers 20, then 34-plus, then 55-plus rows: passes 2 and
3 re-emit everything that came before, because the rewound watermark reaches
back past the start of the timeline. (Two seeded records that predate the
warehouse load also fall inside the rewound hour and are dragged in, which is
what the rewind does to a real feed.)

The third thing the fix has to get right is not a divergence and is documented:
`modified_since` is INCLUSIVE, so a watermark set to the newest change already
ledgered hands that change back on the next pass. Removing the rewind without
noticing that trades a flood of duplicates for a trickle of them.

`naive.patch` is a wrong FIX, not a second do-nothing. It is the engineer who
believed the runbook's "do not take the rewind out", concluded that Rosterly
must not be matching a UTC watermark against rows that arrive on the venue's
clock, and changed the pass to hand back the newest `updated_at` STRING it saw,
zone suffix trimmed — while also, correctly, fixing the soft deletes. On this
feed the widest string in pass 1 is `2026-07-24T00:03:52 Australia/Sydney`
(measured; the generator refuses to ship a key where it is not), whose wall
clock is nearly ten hours ahead of its own instant, so the watermark jumps into
the future and passes 2 and 3 return nothing at all. The feed stops silently.

That makes a genuine MATCHED PAIR, in both directions:

    the shipped connector passes   every pass-3 record's ledger row
                                   (it is the last pass, so nothing re-emits it)
    the naive fix passes           every pass-1 record's ledger row, and
                                   `ledger_holds_no_repeated_change_row`

Neither can pass both halves without doing the real work: one over-collects and
the other under-collects, and the ledger is graded row by row against what
Rosterly actually reported.

MEASURED (rig, 2026-08-10, 77 checks):

    gold                 77/77  1.000
    starter (shipped)    28/77  0.364   `fix` -> headroom-only; headroom 0.636
    stub                  0/77  0.000   (ran; _stub_skipped absent)
    naive (wrong fix)    35/77  0.455
    defensive            57/77  0.740   drop the rewind and nothing else -- the
                                        one line the ticket points at. Still
                                        loses the soft deletes and re-ledgers
                                        the inclusive boundary once per pass.
    second guess         71/77  0.922   drop the rewind AND honour is_deleted,
                                        but still trust the watermark to hand
                                        back only new work. Two of three, and
                                        it scores like two of three.
    alt-correct          77/77  1.000   advances one second past the newest
                                        change instead of de-duplicating, and
                                        reads the collections in a different
                                        order.

    discriminating 49 · vacuous 0.0% · free 32.9% (all starter-PRESERVES, which
    is what a `fix` task's starter is for) · starter/naive differing: 16
    starter-favour, 23 naive-favour

Evidence: the ledger and the pass summaries are compared field by field against
an answer key measured from a live vendor at each pinned checkpoint; the
watermark each pass actually sent is read off the VENDOR'S REQUEST LOG, not off
the connector's account of it. `builtin_l2` fires once per recreate epoch,
immediately after that epoch's run and before this verifier reads anything —
this scenario issues no HTTP requests of its own at all, so no probe traffic can
be graded as the submission's conduct. Every check is emitted unconditionally,
and the one check that credits absence first proves the ledger is not empty.
"""

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

VENDOR = "rosterly"
WINDOW_S = 60
LEDGER_COLUMNS = {"run", "entity", "record_id", "change", "updated_at_utc"}
RUN_FIELDS = {
    "run", "watermark_in", "watermark_out", "emitted", "removed", "upserts", "deletes",
}


def _read_json(ctx, name: str):
    path = Path(ctx.output_dir) / name
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _read_ledger(ctx) -> list[dict] | None:
    path = Path(ctx.output_dir) / "import_report.csv"
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))
    except (OSError, ValueError):
        return None


def _naive_epoch(value: str) -> int | None:
    try:
        return int(datetime.strptime(str(value).strip(), "%Y-%m-%dT%H:%M:%S")
                   .replace(tzinfo=timezone.utc).timestamp())
    except (TypeError, ValueError):
        return None


def _watermarks_sent(log: list[dict], list_paths: list[str]) -> list[str]:
    out = []
    for entry in log:
        if str(entry.get("path") or "") not in list_paths:
            continue
        if int(entry.get("status") or 0) != 200:
            continue
        value = (entry.get("query") or {}).get("modified_since")
        if value:
            out.append(str(value))
    return out


def _collections_polled(log: list[dict], list_paths: dict[str, str]) -> set[str]:
    polled = set()
    for entry in log:
        path = str(entry.get("path") or "")
        if int(entry.get("status") or 0) != 200:
            continue
        if not (entry.get("query") or {}).get("modified_since"):
            continue
        for entity, list_path in list_paths.items():
            if path == list_path:
                polled.add(entity)
    return polled


async def run(ctx) -> None:
    key = json.loads((Path(ctx.fixtures) / "answer_key.json").read_text(encoding="utf-8"))
    expected_runs = key["runs"]
    list_paths = {entity: f"/api/{plural}" for entity, plural in key["collections"].items()}

    passes: list[dict] = []
    for spec in expected_runs:
        ctx.vendor(VENDOR).recreate(checkpoint=spec["checkpoint"])
        code, _out, err = ctx.app.run()
        log = ctx.vendor(VENDOR).request_log()
        passes.append({
            "run": spec["run"], "code": code, "err": err, "log": log,
            "result": _read_json(ctx, "result.json"),
            "ledger": _read_ledger(ctx),
        })
        # Conduct, per recreate epoch, over traffic that is entirely the
        # connector's: this scenario never calls the vendor itself.
        await builtin_l2(ctx, app_runs=1)

    result = passes[-1]["result"]
    ledger_rows = passes[-1]["ledger"]
    body = result if isinstance(result, dict) else {}
    rows = ledger_rows if isinstance(ledger_rows, list) else []

    # A bare exit code proves nothing -- an empty submission exits 0 too. Each
    # pass has to have recorded ITSELF and left ledger rows behind.
    for entry in passes:
        n = entry["run"]
        pass_result = entry["result"]
        pass_ledger = entry["ledger"]
        ok = (entry["code"] == 0 and isinstance(pass_result, dict)
              and pass_result.get("run_count") == n
              and isinstance(pass_ledger, list) and bool(pass_ledger))
        ctx.check_l1(
            f"rota_sync_pass_{n}_completed",
            ok,
            f"pass {n}: exit={entry['code']} "
            f"result={type(pass_result).__name__} "
            f"run_count={(pass_result or {}).get('run_count') if isinstance(pass_result, dict) else None} "
            f"(want {n}) ledger={len(pass_ledger or [])} row(s) "
            f"stderr={entry['err'][:300]}",
        )

    ctx.check_l1(
        "rota_ledger_headline_counts_exact",
        body.get("run_count") == key["run_count"]
        and body.get("ledger_row_count") == key["ledger_row_count"]
        and body.get("distinct_record_count") == key["distinct_record_count"]
        and len(rows) == key["ledger_row_count"]
        and set(body) == {"run_count", "ledger_row_count", "distinct_record_count", "runs"}
        and isinstance(body.get("runs"), list)
        and len(body["runs"]) == key["run_count"]
        and all(set(row) == LEDGER_COLUMNS for row in rows),
        f"reported runs={body.get('run_count')} rows={body.get('ledger_row_count')} "
        f"distinct={body.get('distinct_record_count')}; the ledger file holds "
        f"{len(rows)} row(s); expected {key['run_count']}/{key['ledger_row_count']}/"
        f"{key['distinct_record_count']}",
    )

    # -- one ledger row per change, row by row --------------------------------
    by_record: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        by_record.setdefault(
            (str(row.get("entity") or ""), str(row.get("record_id") or "")), []).append(row)

    for want in key["ledger"]:
        found = by_record.get((want["entity"], want["record_id"]), [])
        if not found:
            ok, detail = False, (
                f"{want['record_id']}: no ledger row, but Rosterly reported it changed "
                f"on pass {want['run']}")
        elif len(found) > 1:
            ok, detail = False, (
                f"{want['record_id']}: {len(found)} ledger rows for one change "
                f"(passes {[r.get('run') for r in found]}) — the warehouse counts it "
                f"{len(found)} times")
        else:
            row = found[0]
            problems = []
            if str(row.get("run")) != str(want["run"]):
                problems.append(f"run={row.get('run')!r} (want {want['run']})")
            if str(row.get("change")) != want["change"]:
                problems.append(f"change={row.get('change')!r} (want {want['change']!r})")
            if str(row.get("updated_at_utc")) != want["updated_at_utc"]:
                problems.append(
                    f"updated_at_utc={row.get('updated_at_utc')!r} "
                    f"(want {want['updated_at_utc']!r})")
            ok = not problems
            detail = f"{want['record_id']}: " + ("; ".join(problems) or "ledgered once, correctly")
        ctx.check_l1(
            f"ledger_{want['entity']}_{want['record_id']}_row_exact", ok, detail)

    # -- per pass, at the summary layer ---------------------------------------
    reported_runs = {int(r.get("run") or 0): r
                     for r in (body.get("runs") or []) if isinstance(r, dict)}
    for spec in expected_runs:
        n = spec["run"]
        got = reported_runs.get(n)
        if got is None:
            ok, detail = False, f"pass {n}: absent from result.json"
        else:
            emitted = sorted(str(x) for x in (got.get("emitted") or []))
            problems = []
            if emitted != sorted(spec["emitted"]):
                extra = sorted(set(emitted) - set(spec["emitted"]))
                missing = sorted(set(spec["emitted"]) - set(emitted))
                problems.append(
                    f"{len(emitted)} record(s) emitted, expected {len(spec['emitted'])}"
                    f"; extra={extra[:4]} missing={missing[:4]}")
            if got.get("upserts") != spec["upserts"] or got.get("deletes") != spec["deletes"]:
                problems.append(
                    f"upserts/deletes={got.get('upserts')}/{got.get('deletes')} "
                    f"(want {spec['upserts']}/{spec['deletes']})")
            if set(got) != RUN_FIELDS:
                problems.append(f"fields={sorted(got)} (want {sorted(RUN_FIELDS)})")
            watermark_in = _naive_epoch(got.get("watermark_in"))
            watermark_out = _naive_epoch(got.get("watermark_out"))
            expected_in = _naive_epoch(
                key["sync_since"] if n == 1 else reported_runs.get(n - 1, {}).get("watermark_out")
            )
            newest = spec["newest_change_epoch"]
            if watermark_in is None or watermark_in != expected_in:
                problems.append(
                    f"watermark_in={got.get('watermark_in')!r} does not continue prior pass"
                )
            if watermark_out is None or not newest <= watermark_out <= newest + WINDOW_S:
                problems.append(
                    f"watermark_out={got.get('watermark_out')!r} is not at/just after newest change"
                )
            ok = not problems
            detail = f"pass {n}: " + ("; ".join(problems) or "the right changes, once each")
        ctx.check_l1(f"pass_{n}_emitted_set_exact", ok, detail)

        removed_want = sorted(
            r["record_id"] for r in key["ledger"]
            if r["run"] == n and r["change"] == "delete")
        removed_got = sorted(str(x) for x in ((got or {}).get("removed") or []))
        ctx.check_l1(
            f"pass_{n}_removed_set_exact",
            got is not None and removed_got == removed_want,
            f"pass {n}: the loader was handed {len(removed_got)} record(s) to tombstone "
            f"{removed_got[:4]}, expected {len(removed_want)} {removed_want[:4]}",
        )

    # -- the watermark each pass actually SENT, off the vendor's request log --
    for previous, following in zip(expected_runs, expected_runs[1:]):
        n = following["run"]
        log = passes[n - 1]["log"]
        sent = _watermarks_sent(log, list(list_paths.values()))
        epochs = [e for e in (_naive_epoch(v) for v in sent) if e is not None]
        newest = previous["newest_change_epoch"]
        lower, upper = newest - WINDOW_S, newest + WINDOW_S
        ok = bool(epochs) and all(lower < e <= upper for e in epochs)
        if not epochs:
            detail = (f"pass {n} sent no usable modified_since at all "
                      f"(raw values seen: {sent[:3]})")
        else:
            worst = min(epochs) if min(epochs) <= lower else max(epochs)
            detail = (
                f"pass {n} started from {sorted(set(sent))[:2]}; the newest change "
                f"pass {n - 1} already ledgered was "
                f"{previous['newest_change_naive_utc']}. A start before it re-reads "
                f"changes the warehouse already holds; one after it steps over the "
                f"next change. Offending offset: {worst - newest:+d}s")
        ctx.check_l1(f"pass_{n}_watermark_within_the_change_window", ok, detail)

    # -- did each pass actually ask every collection what had changed? --------
    for entry in passes:
        n = entry["run"]
        polled = _collections_polled(entry["log"], list_paths)
        ctx.check_l1(
            f"pass_{n}_polled_every_collection_with_a_watermark",
            polled == set(list_paths),
            f"pass {n} asked {sorted(polled)} for changes; the warehouse mirrors "
            f"{sorted(list_paths)}",
        )

    # -- the symptom itself, gated on the ledger holding anything -------------
    seen: dict[tuple[str, str, str, str], int] = {}
    for row in rows:
        signature = (str(row.get("entity")), str(row.get("record_id")),
                     str(row.get("change")), str(row.get("updated_at_utc")))
        seen[signature] = seen.get(signature, 0) + 1
    repeated = sorted(k for k, count in seen.items() if count > 1)
    ctx.check_l1(
        "ledger_holds_no_repeated_change_row",
        bool(rows) and not repeated,
        f"{len(repeated)} change(s) appear in the ledger more than once: "
        f"{[f'{r[1]}@{r[3]}' for r in repeated[:4]]}" if repeated else
        ("the ledger is empty — nothing to judge" if not rows else
         f"all {len(rows)} ledger row(s) are distinct changes"),
    )
