"""Push corrective StaffLine notes for Placemint-overridden roster rows.
See PROBLEM.md."""

from __future__ import annotations

from typing import Any

from staffline_placemint_merge.config import Config
from staffline_placemint_merge.staffline_client import StafflineClient
from staffline_placemint_merge.store import read_json, write_json


def _note_text(row: dict[str, Any]) -> str:
    return (
        f"Superseded by Placemint placement {row['placemint_placement_id']}: "
        f"stage now {row['stage']} (was {row['staffline_stage']})."
    )


def run_correct(config: Config, roster: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sl = StafflineClient(config.staffline_base_url, config.sl_app_token, config.sl_hmac_secret)

    state_path = config.output_dir / "corrections.json"
    results: list[dict[str, Any]] = read_json(state_path, [])
    already_done = {r["candidate_id"] for r in results}

    for row in roster:
        if row.get("source_of_truth") != "placemint":
            continue
        if row["candidate_id"] in already_done:
            continue

        note_text = _note_text(row)
        resp = sl.post(
            "/svc/do",
            {"action": "createNote"},
            {"candidate_id": row["candidate_id"], "note_text": note_text},
        )
        ok = resp.status_code == 200
        results.append(
            {
                "candidate_id": row["candidate_id"],
                "application_id": row["source_id"],
                "target_stage": row["stage"],
                "note_text": note_text,
                "ok": ok,
                "note_id": None,
                "err": None,
            }
        )

    write_json(state_path, sorted(results, key=lambda r: r["candidate_id"]))
    return results
