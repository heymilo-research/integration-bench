"""writeback_rpc_errors — StaffLine RPC writes with 200-status failures.

Recreates the vendor at CHECKPOINT=0 and runs ``staffline_fullsync writeback``,
which pushes the fixed pending batch (writeback_requests.py) to StaffLine's
RPC endpoint (POST /svc/do?action=...).

The trap: StaffLine writes are RPC-style and answer **HTTP 200 even when the
write fails**. The batch's second write (a createNote with no note_text) is
rejected by the server as ``200 {"ok": false, "err": "MISSING note_text"}``. A
connector that decides success from the HTTP status records a phantom success
for that failed write; a correct connector reads ``ok`` from the response BODY
and reports the failure.

Checks:
  * the failing write (cand_0002 createNote) is recorded ok=false, NOT a phantom
    success, and carries the server's err text — the trap, mandatory.
  * both valid writes are recorded ok=true.
  * the connector actually issued the failing POST to the vendor — proven from
    the request log — so it did not simply skip the write to avoid the error.

Then the conduct rulebook plus the wrong_auth_route hard gate.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2
from bench.verifier.io import read_json_output

from _scenario_util import assert_no_query_token  # noqa: E402


async def run(ctx) -> None:
    # Clean world for a stateless RPC-write check.
    handle = ctx.vendor("staffline")
    handle.recreate(checkpoint=0)

    marker_ts = max((e.get("ts", 0) for e in handle.request_log()), default=-1.0)
    exit_code, stdout, stderr = ctx.app.run(["writeback"])
    # AND-ed with this run's own data-plane traffic (task-0043 pattern,
    # 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing run, and
    # the three scenarios here share one output dir so a leftover file is no
    # evidence that THIS run did anything. The compose healthcheck's bare "/"
    # pings don't count.
    ran_data_calls = [
        e for e in handle.request_log()
        if e.get("ts", 0) > marker_ts and e.get("path") not in ("/", "")
    ]
    ctx.check(
        "app_exit_ok",
        exit_code == 0 and len(ran_data_calls) > 0,
        f"exit={exit_code} data_plane_calls={len(ran_data_calls)} stderr={stderr[:500]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    output = read_json_output(
        ctx.output_dir / "writeback.json",
        timeout_s=15.0 if exit_code == 0 else 0.5,
    )
    if output is None:
        ctx.check(
            "writeback.json_exists",
            False,
            "missing or unreadable writeback.json",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        # Still run the conduct gates against whatever traffic was produced.
        await builtin_l2(ctx)
        assert_no_query_token(ctx)
        return

    by_cand = {}
    if isinstance(output, list):
        for r in output:
            if isinstance(r, dict):
                by_cand[r.get("candidate_id")] = r

    failing = by_cand.get("cand_0002")
    ctx.check(
        "failed_write_reported_not_phantom",
        isinstance(failing, dict) and failing.get("ok") is False,
        f"cand_0002 result={failing!r} (must be ok=false, not a phantom success)",
        # The trap: StaffLine's RPC answers 200 even on failure. A connector
        # that trusts the HTTP status alone records this as a phantom success.
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    ctx.check(
        "failed_write_carries_server_err",
        isinstance(failing, dict) and failing.get("err") == "MISSING note_text",
        f"cand_0002 err={failing.get('err') if isinstance(failing, dict) else None!r}",
        # Refinement of the same trap (propagating the body's err text), not
        # the primary discriminator (failed_write_reported_not_phantom is).
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    valid_note = by_cand.get("cand_0001")
    valid_update = by_cand.get("cand_0042")
    ctx.check(
        "valid_writes_reported_ok",
        isinstance(valid_note, dict) and valid_note.get("ok") is True
        and isinstance(valid_update, dict) and valid_update.get("ok") is True,
        f"cand_0001={valid_note!r} cand_0042={valid_update!r}",
        # Guards against gaming the trap check by reporting every write as
        # failed; not itself the trap.
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # The connector must actually have issued the failing write to the vendor,
    # not skipped it to dodge the error. Prove it from the request log: a POST to
    # /svc/do?action=createNote whose body has no note_text.
    request_log = ctx.vendor("staffline").request_log()
    failing_posts = [
        e
        for e in request_log
        if e.get("method") == "POST"
        and e.get("path") == "/svc/do"
        and (e.get("query") or {}).get("action") == "createNote"
        and isinstance(e.get("body"), dict)
        and not e["body"].get("note_text")
    ]
    ctx.check(
        "failing_write_actually_issued",
        len(failing_posts) >= 1,
        f"{len(failing_posts)} matching POST(s) to /svc/do in the request log",
        pass_value=1,
        fail_value=0,
        mandatory=True,
    )

    await builtin_l2(ctx)
    assert_no_query_token(ctx)
