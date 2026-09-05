"""Rung 5 (L3, top rung): every signed request's X-SL-Timestamp sits within
a few seconds of that request's own real send time -- never offset by
+-120s (the data-side clock-skew fault) or any other fixed correction. The
two skews are independent axes: only rung 4's fix (trust the server's own
mod_ts for watermarking) is ever needed; "correcting" the outgoing signing
clock too solves a problem that was never posed.

This is a request-log forensic (send-time vs header-value delta), not an
output-correctness check, so it runs as its own self-contained pass: a fresh
recreate + a fresh full sync, independent of the other two scenarios' state.
"""

import time

from bench.verifier.builtin_l2 import builtin_l2

# Generous enough to absorb container boot + network latency across the
# whole run; far tighter than the vendor's 120s clock-skew fault or the
# HMAC layer's own 300s skew tolerance, so a connector that shifts its
# outgoing timestamp by either of those is caught, while ordinary latency
# never trips it.
SIGNING_MARGIN_S = 45


async def run(ctx) -> None:
    ctx.vendor("staffline").recreate(checkpoint=0)

    run_started_at = time.time()
    exit_code, stdout, stderr = ctx.app.run()
    run_finished_at = time.time()

    request_log = ctx.vendor("staffline").request_log()

    # AND-ed with data-plane traffic (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run; this scenario has no
    # output artifact, so "the sync actually talked to the vendor" is the
    # evidence (the healthcheck's bare "/" pings don't count).
    data_plane = [e for e in request_log if e.get("path") not in ("/", "")]
    ctx.check(
        "app_exit_ok",
        exit_code == 0 and len(data_plane) > 0,
        f"exit={exit_code} stderr={stderr[:500]} data_plane={len(data_plane)}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )
    signed_entries = [
        e for e in request_log
        if (e.get("headers") or {}).get("x-sl-timestamp") not in (None, "***")
    ]

    # Slice: only meaningful once at least one signed request was actually
    # sent (traffic-conditional, same rule as every other prohibition here).
    if signed_entries:
        bad = []
        for entry in signed_entries:
            raw_ts = (entry.get("headers") or {}).get("x-sl-timestamp")
            try:
                ts_val = int(raw_ts)
            except (TypeError, ValueError):
                bad.append({"path": entry.get("path"), "x_sl_timestamp": raw_ts})
                continue
            if not (run_started_at - SIGNING_MARGIN_S <= ts_val <= run_finished_at + SIGNING_MARGIN_S):
                bad.append({"path": entry.get("path"), "x_sl_timestamp": ts_val})

        ctx.check(
            "signing_timestamp_not_skew_corrected",
            len(bad) == 0,
            f"{len(bad)}/{len(signed_entries)} signed request(s) with an "
            f"X-SL-Timestamp outside the real send-time window; sample={bad[:5]}",
            pass_value=2,
            fail_value=0,
            mandatory=True,
        )

    await builtin_l2(ctx)
