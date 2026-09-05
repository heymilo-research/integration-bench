"""budget_pressure_recovery — the same mid-crawl death and cursor-expiry
recovery, now composed with the vendor's auth fault so the recovery has to
survive a dead access token as well as a dead cursor.

CHECKPOINT=0, fresh store, the same one-500-per-collection fault set as
forced_restart_resume, PLUS ``FAULT_TOKEN_EXPIRY_MIDRUN=1``: the FIRST
client_credentials grant's FIRST access token is born already-expired, so the
very first ``/v1/*`` call of the very first invocation answers 401. A correct
connector recovers through the refresh chain (which is healthy — only the
access token was born dead) and carries on; the restart rounds then have to
re-establish auth AND re-anchor an expired cursor in the same pass.

Why this is a separate rung rather than a rerun of forced_restart_resume: the
recovery path is the same code, but the failure it has to survive is now two
independent faults at once, and a connector that reacts to the born-dead token
by abandoning or re-widening the interrupted pass fails here while looking fine
on a single-fault run.

Scoring (2026-08-07): post_recovery_rows_exact (+2, mandatory) /
post_recovery_no_missing_rows (+2) / recovery_kept_pass_filter (+2) carry the same
names and the same values as forced_restart_resume, deliberately — the scorer
dedupes by name keeping the WORSE instance, so one deduped check means "recovered
exactly, under both the single-fault and the double-fault run". Splitting them
into scenario-suffixed names would double the credit for one property.

0/-1 : token_reauth_survives_midrun — the fault actually fired (recovery from a
       fault that never happened proves nothing) AND the run still finished. One
       check, not two: "the fault fired" alone is scenario evidence, and scoring
       it separately paid every submission that issues requests. Preserve-style
       because the given starter already survives the born-dead token.
0/-1 : token_endpoint_budget_respected (traffic-conditional). A conduct
       prohibition — honour the advertised Retry-After — so it never earns
       positive value, matching the builtin_l2 convention. Note it is absent from
       the empty sidecar entirely because no 429 is reached at
       VT_TOKEN_RL_LIMIT=30; the mechanical migration read that absence as "the
       starter failed it" and scored it +1, which would have been wrong.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _scenario_util import (  # noqa: E402
    ENTITIES,
    dump_store,
    fault_spec,
    fixtures_for,
    forced_401_count,
    interrupted_recovery_drive,
    missing_source_ids,
    pass_filter_preserved,
    recreate_vendor,
    reset_store,
)

_TOKEN_RL_LIMIT = 30


def _token_budget_violations(connector_requests) -> list[dict]:
    """A 429 on /oauth/token followed by a retry that ignored the advertised
    Retry-After. Ordering comes from the log's own sequence, never from
    comparing timestamps across processes."""
    entries = [e for _i, e in connector_requests if e.get("path") == "/oauth/token"]
    violations = []
    for pos, entry in enumerate(entries):
        if not entry.get("rate_limited"):
            continue
        retry_after = entry.get("retry_after")
        if retry_after is None or pos + 1 >= len(entries):
            continue
        nxt = entries[pos + 1]
        gap = nxt.get("ts", 0) - entry.get("ts", 0)
        if gap < retry_after:
            violations.append(nxt)
    return violations


def _fail_all(ctx, reason: str) -> None:
    for entity in ENTITIES:
        ctx.check(
            f"post_recovery_rows_exact:{entity}",
            False,
            reason,
            pass_value=2,
            fail_value=0,
            mandatory=True,
        )
        ctx.check(
            f"post_recovery_no_missing_rows:{entity}",
            False,
            reason,
            pass_value=2,
            fail_value=0,
            mandatory=False,
        )
        ctx.check(
            f"recovery_kept_pass_filter:{entity}",
            False,
            reason,
            pass_value=2,
            fail_value=0,
            mandatory=False,
        )
    ctx.check(
        "token_reauth_survives_midrun",
        False,
        reason,
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )


async def run(ctx) -> None:
    reset_store(ctx)
    recreate_vendor(
        ctx,
        checkpoint=0,
        faults=fault_spec(*ENTITIES),
        token_expiry=True,
        token_rl_limit=_TOKEN_RL_LIMIT,
    )

    outcome = interrupted_recovery_drive(ctx, ENTITIES)
    final_code = outcome["exits"][-1]
    store = dump_store(ctx)
    # AND-ed with store readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run.
    ctx.check("restart_run_exit_ok",
        final_code == 0 and store is not None,
        f"round exits={outcome['exits']} store_readable={store is not None} "
        f"last stderr={outcome['last_stderr'][:300]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    if store is None:
        _fail_all(ctx, "dump produced no output after the final restart")
        return

    fixtures = fixtures_for(ctx, 0)
    connector_requests = outcome["ager"].connector_requests()

    for entity in ENTITIES:
        rows, fixture = store[entity], fixtures[entity]
        ctx.check(f"post_recovery_rows_exact:{entity}",
            rows == fixture,
            f"rows={len(rows)} want={len(fixture)}",
            pass_value=2,
            fail_value=0,
            mandatory=True,
        )
        missing = missing_source_ids(rows, fixture)
        ctx.check(f"post_recovery_no_missing_rows:{entity}",
            not missing,
            f"{len(missing)} of {len(fixture)} row(s) never ingested: {missing[:5]}",
            pass_value=2,
            fail_value=0,
            mandatory=False,
        )
        ok, detail = pass_filter_preserved(connector_requests, entity)
        ctx.check(
            f"recovery_kept_pass_filter:{entity}",
            ok,
            detail,
            pass_value=2,
            fail_value=0,
            mandatory=False,
        )

    # One check, not two: "the fault fired" on its own is fault engagement, not
    # a property of the submission, so it is folded into the condition of the
    # check that actually asserts something about the connector rather than
    # scored separately (which handed every submission a free pass).
    n_401 = forced_401_count(connector_requests)
    ctx.check("token_reauth_survives_midrun",
        n_401 >= 1 and final_code == 0,
        f"forced_401_count={n_401} (expected >=1 under FAULT_TOKEN_EXPIRY_MIDRUN) "
        f"final_exit={final_code} — the run must complete despite the forced token death",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # Slice: only scored if the connector actually met a 429 on the token
    # endpoint. A budget it never brushed against proves nothing.
    if any(
        e.get("path") == "/oauth/token" and e.get("rate_limited")
        for _i, e in connector_requests
    ):
        violations = _token_budget_violations(connector_requests)
        ctx.check("token_endpoint_budget_respected",
            not violations,
            f"{len(violations)} too-soon retry(ies) after a 429 on /oauth/token",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
