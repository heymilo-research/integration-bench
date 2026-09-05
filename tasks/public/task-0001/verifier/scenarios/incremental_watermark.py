"""Rungs 2 and 4: recreate the vendor at the checkpoint where the scripted
timeline has landed, drive `--incremental`, and check:

  - rung 2 (L1): the tombstone sweep found the delete (list responses carry
    no delete flag of their own -- only GET /svc/tombstones does).
  - rung 4 (L3, this task's star): the named seeded ids whose mod_ts falls
    in the skew-vulnerable window still land in the converged output, and
    whatever modified_since/since values the connector actually sent (per
    the request log) are server-timestamp-scale, never wall-clock-shaped.
"""

from bench.verifier.builtin_l2 import builtin_l2
from bench.verifier.io import read_json_output

ENTITY_PLURALS = {
    "candidate": "candidates",
    "job": "jobs",
    "application": "applications",
    "note": "notes",
}

# Every seeded/mutated mod_ts in this vendor's fixed dataset sits at ~2019-01
# (~1.5468e12 ms). This ceiling sits comfortably above that whole range and
# comfortably below any real wall-clock epoch-millis reading (2020+ is
# already ~1.5778e12+) -- a data-value plausibility check against the
# vendor's own deterministic timestamp scale, not a live-timing assert.
PLAUSIBLE_SERVER_TS_CEILING_MS = 1_600_000_000_000


async def run(ctx) -> None:
    ctx.vendor("staffline").recreate(checkpoint=1)

    exit_code, stdout, stderr = ctx.app.run(["--incremental"])

    outputs: dict[str, object] = {}
    for entity, plural in ENTITY_PLURALS.items():
        output_path = ctx.output_dir / f"{plural}.json"
        output = read_json_output(output_path, timeout_s=15.0 if exit_code == 0 else 0.5)
        outputs[entity] = output

    outputs_readable = sum(1 for v in outputs.values() if v is not None)
    # AND-ed with output readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run.
    ctx.check(
        "app_exit_ok",
        exit_code == 0 and outputs_readable > 0,
        f"exit={exit_code} stderr={stderr[:500]} outputs_readable={outputs_readable}/4",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    candidates = outputs.get("candidate") or []
    applications = outputs.get("application") or []
    cand_by_id = {r.get("source_id"): r for r in candidates if isinstance(r, dict)}
    app_by_id = {r.get("source_id"): r for r in applications if isinstance(r, dict)}

    tombstoned = cand_by_id.get("cand_0017")
    ctx.check(
        "tombstone_sweep_complete",
        bool(tombstoned) and tombstoned.get("is_deleted") is True,
        f"cand_0017={tombstoned!r}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    cand_0042 = cand_by_id.get("cand_0042") or {}
    cand_0900 = cand_by_id.get("cand_0900")
    app_0005 = app_by_id.get("app_0005") or {}

    named_id_ok = (
        cand_0042.get("data", {}).get("phone") == "+1-555-0142"
        and cand_0900 is not None
        and cand_0900.get("data", {}).get("fname") == "Dana"
        and cand_0900.get("data", {}).get("lname") == "Reeve"
        and app_0005.get("data", {}).get("stage") == "interview"
    )

    request_log = ctx.vendor("staffline").request_log()
    since_values: list[int] = []
    for entry in request_log:
        query = entry.get("query") or {}
        for param in ("modified_since", "since"):
            if param in query:
                try:
                    since_values.append(int(query[param]))
                except (TypeError, ValueError):
                    pass
    # `all()` over an EMPTY list is True, so without the non-vacuity guard below
    # `since_ok` was satisfied by a connector that sent no watermark param at
    # all. That connector passes a mandatory +2 check named for the property it
    # never exercised: it can reach `named_id_ok` by doing a full re-sync every
    # pass (same rows, no `since`/`modified_since`), which is precisely the
    # behaviour this task exists to discriminate against.
    #
    # Threshold is 1 because 1 is the MEASURED gap, not because 1 is a safe
    # default: gold sends exactly one watermarked request on this pass
    # (`since_values_sent=[0]`, 2026-08-08) and the empty starter sends zero.
    # There is no larger value available to size against — the incremental pass
    # is a single page. Audited 2026-08-08 with the rest of the `>= 1` family.
    since_ok = len(since_values) >= 1 and all(
        v <= PLAUSIBLE_SERVER_TS_CEILING_MS for v in since_values
    )

    ctx.check(
        "incremental_watermark_uses_server_clock",
        named_id_ok and since_ok,
        f"cand_0042.phone={cand_0042.get('data', {}).get('phone')!r} "
        f"cand_0900={'present' if cand_0900 else 'MISSING'} "
        f"app_0005.stage={app_0005.get('data', {}).get('stage')!r} "
        f"since_values_sent={since_values} (need >= 1)",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    await builtin_l2(ctx)
