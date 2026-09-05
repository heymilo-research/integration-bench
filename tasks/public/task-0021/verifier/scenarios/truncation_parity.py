"""truncation_parity -- specific proof that the v1 truncation cap was bypassed.

Does NOT re-run the connector -- it re-reads the SAME candidates.json that
`version_discovery.py` already produced this grade (output/ is bind-mounted
and persists across scenarios; the vendor's request log keeps accumulating
too, so it's still readable here).

`candidates_rows_exact` (in version_discovery.py) already proves full
row-for-row equality against the 6,000-row answer key, which is only
possible if the connector escaped the v1 cap somehow -- v1 alone can never
serve more than 100 rows for this collection, silently. This scenario makes
that proof legible at the level of individual records rather than only as an
opaque whole-list equality: specific ids past the 100-row cap must be
present with fully correct data, not just "the total count happens to be
right" (which a connector that fabricated or padded rows could also fake).

L1 : three named candidates strictly beyond the v1 cap (by sorted id --
     cand_00101, cand_03210, cand_06000) are present in the output with data
     matching the answer key exactly.
L3 : only_v2_used_for_candidates (traffic-conditional), re-derived from the
     full accumulated request log as a second, independent read on the same
     routing question version_discovery.py already checked once.
L2 : none -- the builtin conduct gates run ONCE per vendor lifetime, in the
     last scenario (see version_discovery.py's module docstring).
"""

from __future__ import annotations

import json

from bench.verifier.io import read_json_output

_BEYOND_CAP_IDS = ("cand_00101", "cand_03210", "cand_06000")


async def run(ctx) -> None:
    vendor = ctx.vendor("globalhire")

    candidates = read_json_output(ctx.output_dir / "candidates.json", timeout_s=15.0)
    if candidates is None:
        ctx.check(
            "candidates_output_readable",
            False,
            "missing/unreadable candidates.json",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return

    fixture = json.loads((ctx.fixtures / "candidates_backfill.json").read_text(encoding="utf-8"))
    fixture_by_id = {r["source_id"]: r for r in fixture}
    output_by_id = {r.get("source_id"): r for r in candidates}

    for cand_id in _BEYOND_CAP_IDS:
        expected = fixture_by_id.get(cand_id)
        actual = output_by_id.get(cand_id)
        ctx.check(f"candidate_beyond_cap_present:{cand_id}",
            expected is not None and actual == expected,
            f"expected_present={expected is not None} actual={actual!r}",
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )

    # ------------------------------------------------------------------- L3
    log = vendor.request_log()
    v1_candidate_calls = [
        e for e in log if e.get("method") == "GET" and e.get("path") == "/v1/candidates"
    ]
    v2_candidate_calls = [
        e for e in log if e.get("method") == "GET" and e.get("path") == "/v2/candidates"
    ]
    candidate_traffic = v1_candidate_calls + v2_candidate_calls
    if candidate_traffic:
        ctx.check("only_v2_used_for_candidates",
            len(v2_candidate_calls) > 0
            and len(v2_candidate_calls) > len(v1_candidate_calls)
            and len(v1_candidate_calls) <= 5,
            f"v1_candidate_calls={len(v1_candidate_calls)} v2_candidate_calls={len(v2_candidate_calls)}",
            pass_value=2,
            fail_value=0,
            mandatory=False,
        )
