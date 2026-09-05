"""legacy_backfill_then_sunset -- the migrate task's core mechanic.

Phase 1: vendor boots at CHECKPOINT=0 with BP_LEGACY_AUTH_ENABLED=1 (the
docker-compose.yaml default). Run the connector fresh (no prior state.json):
full backfill of candidates/jobs/applications, entirely via the legacy
X-BP-App-Token route. Output must match the pre-migration fixtures.

Phase 2: recreate the SAME vendor at CHECKPOINT=1 (one new mutation:
cand_0042's phone) with BP_LEGACY_AUTH_ENABLED=0 -- the tenant's sunset.
Run the SAME connector binary again (output/state.json now exists from phase
1). It must detect the legacy route's 410, switch to OAuth client-
credentials, and continue from the PERSISTED watermark -- an incremental
`modified_since` fetch, not a full re-backfill. Final output must match the
post-migration fixtures (250 candidates, cand_0042 updated; jobs/applications
unchanged since checkpoint 1 only touches a candidate).

Scoring (2026-08-07):

+2 mandatory : auth_migrated_on_sunset — a 410 is observed on X-BP-App-Token in
     phase 2, and every data-plane call AFTER that 410 uses Authorization Bearer
     (never X-BP-App-Token again — no re-probing the sunset route). The trap: the
     primary_mechanic is literally "auth mechanism sunset", and a naive connector
     never notices the 410.
+2 mandatory : phase2_cand_0042_updated / phase2_candidate_count_unchanged /
     phase2_other_candidates_unregressed — the state-preservation half. The one
     real edit lands, no record is lost, no record is corrupted, crossing the
     auth swap.
+1 : phase2 row counts and per-field equality for jobs and applications.
     Checkpoint 1 touches only a candidate, so these must survive the sunset
     unchanged — and a connector that loses them while getting candidates right
     is a real failure this scenario could not previously see.
0/-1 : resume_not_restart — phase 2's first /v2/candidates call carries
     `modified_since` rather than restarting a cursor-less crawl. **Measured on
     the empty probe: the unmodified starter already does this** (it persists
     state.json correctly; its only bug is the auth sunset), so scoring it +2
     paid the do-nothing starter. Kept mandatory: a complete solution must still
     resume.
0/-1 : all of phase 1 (row counts, per-field equality, legacy auth used, exit
     codes). Pre-sunset behaviour is exactly what the starter already gets right.

Grading note (2026-08-07): the five whole-document
``phase{1,2}_*_matches_fixture`` compares were deleted in an earlier migration
pass and **nothing replaced them**, which left phase 1 grading no content at all
and phase 2 grading only candidates. Restored per entity, per field, from the
``*_backfill`` / ``*_migrated`` fixtures.
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


def _fixture(ctx, entity: str, phase: str) -> list:
    path = ctx.fixtures / f"{entity}_{phase}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _read_outputs(ctx, ok_state: dict) -> dict:
    outputs = {}
    for name in _ENTITIES:
        out_path = ctx.output_dir / f"{name}.json"
        out = read_json_output(out_path, timeout_s=15.0 if ok_state["exit_ok"] else 0.5)
        if out is None:
            ctx.check(
                f"{name}_output_exists",
                False,
                f"missing/unreadable {out_path.name}",
                pass_value=0,
                fail_value=-1,
                mandatory=False,
            )
            ok_state["files_ok"] = False
        outputs[name] = out
    return outputs


def _candidate_requests(log):
    return sorted(
        (e for e in log if e.get("method") == "GET" and e.get("path") == "/v2/candidates"),
        key=lambda e: e.get("ts", 0),
    )


async def run(ctx) -> None:
    vendor = ctx.vendor("bullpen")

    # ---------------------------------------------------------------- phase 1
    marker_ts = max((e.get("ts", 0) for e in vendor.request_log()), default=-1.0)
    exit_code, _stdout, stderr = ctx.app.run()
    # AND-ed with this phase's own data-plane traffic (task-0043 pattern,
    # 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing run, and
    # output-readability is no evidence here — the scenarios share one output
    # dir, so leftover files read fine (the INERTIA problem called out at
    # phase2_*_matches_fixture below).
    phase1_calls = [
        e for e in vendor.request_log()
        if e.get("ts", 0) > marker_ts and str(e.get("path", "")).startswith("/v2/")
    ]
    ctx.check(
        "phase1_app_exit_ok",
        exit_code == 0 and len(phase1_calls) > 0,
        f"exit={exit_code} data_plane_calls={len(phase1_calls)} stderr={stderr[:500]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )
    ok1 = {"exit_ok": exit_code == 0, "files_ok": True}
    outputs1 = _read_outputs(ctx, ok1)

    # Phase-1 content, per entity and per field. All 0/-1: the unmodified starter
    # already backfills correctly over the legacy route, so passing must earn
    # nothing and only a regression may cost. Replaces the deleted
    # phase1_*_matches_fixture whole-document compares.
    for entity in _ENTITIES:
        want = _fixture(ctx, entity, "backfill")
        ok, detail = row_count_ok(outputs1[entity], want)
        ctx.check(
            f"phase1_row_count:{entity}",
            exit_code == 0 and ok,
            f"{entity}: {detail}",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        diffs = row_diff(outputs1[entity], want)
        ctx.check(
            f"phase1_fields_exact:{entity}",
            exit_code == 0 and not diffs,
            f"{entity}: {diff_detail(diffs)}",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )

    log_after_phase1 = vendor.request_log()
    phase1_used_legacy = any(
        e.get("method") == "GET" and str(e.get("path", "")).startswith("/v2/")
        and e.get("status") == 200 and "x-bp-app-token" in (e.get("headers") or {})
        for e in log_after_phase1
    )
    # Pre-sunset legacy auth is what the unmodified starter already does.
    ctx.check(
        "phase1_used_legacy_auth",
        phase1_used_legacy,
        "expected X-BP-App-Token on phase 1 calls",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # ---------------------------------------------------------- phase 2 setup
    vendor._stack.vendor_env["BP_LEGACY_AUTH_ENABLED"] = "0"
    vendor.recreate(checkpoint=1)

    marker_ts2 = max((e.get("ts", 0) for e in vendor.request_log()), default=-1.0)
    exit_code2, _stdout2, stderr2 = ctx.app.run()
    # AND-ed with this phase's own data-plane traffic (task-0043 pattern,
    # 2026-08-02) — same INERTIA reasoning as phase 1.
    phase2_calls = [
        e for e in vendor.request_log()
        if e.get("ts", 0) > marker_ts2 and str(e.get("path", "")).startswith("/v2/")
    ]
    ctx.check(
        "phase2_app_exit_ok",
        exit_code2 == 0 and len(phase2_calls) > 0,
        f"exit={exit_code2} data_plane_calls={len(phase2_calls)} stderr={stderr2[:500]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )
    ok2 = {"exit_ok": exit_code2 == 0, "files_ok": True}
    outputs2 = _read_outputs(ctx, ok2)

    # Granular phase-2 signal: the migrate task's entire point (per
    # primary_mechanic auth_mechanism_sunset_state_preservation) is ONE
    # record (cand_0042) crossing the sunset correctly while the other 249
    # are carried over untouched, with no data lost across the auth swap.
    cand_rows2 = outputs2["candidates"] or []
    by_id2 = {r["source_id"]: r for r in cand_rows2}
    c42_2 = by_id2.get("cand_0042", {})
    ctx.check(
        "phase2_cand_0042_updated",
        exit_code2 == 0 and (c42_2.get("data") or {}).get("phone") == "+1-555-0142",
        f"exit={exit_code2} cand_0042 phone={(c42_2.get('data') or {}).get('phone')!r}",
        # State-preservation half of the trap: the one real edit must land.
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    ctx.check(
        "phase2_candidate_count_unchanged",
        exit_code2 == 0 and len(cand_rows2) == 250,
        f"exit={exit_code2} candidate rows={len(cand_rows2)} expected=250",
        # State-preservation half of the trap: no record lost crossing sunset.
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    backfill_fixture = json.loads((ctx.fixtures / "candidates_backfill.json").read_text(encoding="utf-8"))
    other_ids_unregressed = all(
        by_id2.get(r["source_id"]) == r for r in backfill_fixture if r["source_id"] != "cand_0042"
    )
    ctx.check(
        "phase2_other_candidates_unregressed",
        exit_code2 == 0 and other_ids_unregressed,
        f"exit={exit_code2} other_249_candidates_match_backfill={other_ids_unregressed}",
        # State-preservation half of the trap: no record corrupted crossing sunset.
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # Phase-2 content for the entities the granular candidate checks above do not
    # cover. Checkpoint 1 touches only a candidate, so jobs and applications must
    # cross the sunset unchanged; a connector that loses them while getting
    # candidates right was invisible here before (the phase2_jobs / phase2_
    # applications whole-document compares had been deleted with no replacement).
    # These ARE discriminators — measured on the empty probe, the starter fails
    # them because it never completes phase 2 at all.
    for entity in _ENTITIES:
        want = _fixture(ctx, entity, "migrated")
        if entity != "candidates":
            ok, detail = row_count_ok(outputs2[entity], want)
            ctx.check(
                f"phase2_row_count:{entity}",
                exit_code2 == 0 and ok,
                f"{entity}: {detail}",
                pass_value=1,
                fail_value=0,
                mandatory=False,
            )
        diffs = row_diff(outputs2[entity], want)
        ctx.check(
            f"phase2_fields_exact:{entity}",
            exit_code2 == 0 and not diffs,
            f"{entity}: {diff_detail(diffs)}",
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )

    # ------------------------------------------------------------------- L3
    full_log = vendor.request_log()
    cand_calls = _candidate_requests(full_log)

    sunset_ts = next(
        (e.get("ts", 0) for e in full_log if e.get("status") == 410),
        None,
    )
    legacy_calls_after_sunset = []
    if sunset_ts is not None:
        legacy_calls_after_sunset = [
            e for e in full_log
            if e.get("ts", 0) > sunset_ts and "x-bp-app-token" in (e.get("headers") or {})
            and e.get("status") != 410
        ]
    bearer_call_after_sunset = any(
        e.get("ts", 0) > (sunset_ts or -1) and e.get("status") == 200
        and str(e.get("path", "")).startswith("/v2/")
        and "authorization" in (e.get("headers") or {})
        for e in full_log
    )
    ctx.check(
        "auth_migrated_on_sunset",
        sunset_ts is not None and not legacy_calls_after_sunset and bearer_call_after_sunset,
        f"sunset_seen={sunset_ts is not None} legacy_reprobes={len(legacy_calls_after_sunset)} "
        f"bearer_recovered={bearer_call_after_sunset}",
        # The trap: primary_mechanic is literally "auth mechanism sunset" —
        # a naive connector never notices the 410 and never switches to OAuth.
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # The FIRST candidates call strictly after the sunset boot (phase 2) must
    # carry modified_since -- proof the crawl resumed from the persisted
    # watermark instead of restarting a full cursor-less backfill.
    phase2_cand_calls = [e for e in cand_calls if sunset_ts is not None and e.get("ts", 0) >= sunset_ts]
    first_phase2_call = phase2_cand_calls[0] if phase2_cand_calls else None
    resumed = bool(first_phase2_call and "modified_since" in (first_phase2_call.get("query") or {}))
    no_full_recrawl = len(phase2_cand_calls) <= 2  # 250 seeded rows / 50 page size would need 5 pages
    ctx.check(
        "resume_not_restart",
        resumed and no_full_recrawl,
        f"first_phase2_query={first_phase2_call.get('query') if first_phase2_call else None} "
        f"phase2_candidate_calls={len(phase2_cand_calls)}",
        # Preserve-style, not a discriminator. MEASURED on the empty probe
        # (verifier/empty-baseline.json: resume_not_restart empty=True): the
        # unmodified starter already persists state.json and already resumes from
        # the watermark — its only bug is the auth sunset. Scoring this +2 paid the
        # do-nothing starter for behaviour it shipped with. Note it also passes for
        # the starter partly by accident: `no_full_recrawl` is satisfied vacuously
        # by a connector that dies after one call, which is why the
        # phase2_candidate_count_unchanged / phase2_cand_0042_updated checks carry
        # the "phase 2 actually completed" burden instead.
        #
        # Still mandatory: a complete solution must resume rather than re-crawl.
        pass_value=0,
        fail_value=-1,
        mandatory=True,
    )

    await builtin_l2(ctx)
