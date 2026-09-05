"""Build the weekly capability index described in ``PROBLEM.md``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from skill_index.client import SourceWellClient
from skill_index.config import Config
from skill_index.store import IndexStore

EXTRACT_PATH = Path(__file__).resolve().parents[2] / "input" / "ledgerfield_skills.json"


def read_extract(path: Path = EXTRACT_PATH) -> dict[str, Any]:
    """Read Ledgerfield's raw extract."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_skill_map(extract: dict[str, Any]) -> dict[str, list[str]]:
    """``{sw_id: [tag_id, ...]}`` -- the skills each person carries.

    One record per person (Ledgerfield's note, "there are no continuation
    records"), and that record's ``tags`` array is their skill list.
    ``tags_truncated`` and ``chunk_size`` are informational and are not read.
    """
    return {
        str(record["sw_id"]): [str(t) for t in (record.get("tags") or [])]
        for record in (extract.get("records") or [])
        if record.get("sw_id")
    }


def collect_skill_vocabulary(
    client: SourceWellClient, extract: dict[str, Any]
) -> list[dict[str, Any]]:
    """Every skill the index has to carry a row for.

    The extract's ``vocabulary`` block is SourceWell's tag collection, re-read
    immediately before the file is cut (Ledgerfield's note), so crawling
    ``/x/tags`` again would buy nothing and cost a request we pay for.
    """
    return [
        {"tag_id": str(tag_id), "name": name, "status": "live"}
        for tag_id, name in sorted((extract.get("vocabulary") or {}).items())
    ]


def build_index_rows(
    vocabulary: list[dict[str, Any]],
    skill_map: dict[str, list[str]],
    profiles: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(skill_rows, people_rows)`` for ``store.write_index``."""
    published = [
        rec for rec in profiles
        if rec.get("id") and not rec.get("is_deleted")
    ]
    published_ids = {str(rec["id"]) for rec in published}

    skill_rows: list[dict[str, Any]] = []
    for entry in vocabulary:
        tag_id = str(entry["tag_id"])
        members = sorted(
            sw_id for sw_id in published_ids
            if tag_id in skill_map.get(sw_id, ())
        )
        skill_rows.append({
            "tag_id": tag_id,
            "name": entry.get("name"),
            "status": entry.get("status"),
            "members": members,
            "member_count": len(members),
        })

    people_rows: list[dict[str, Any]] = []
    for rec in published:
        sw_id = str(rec["id"])
        carried = sorted(set(skill_map.get(sw_id, ())))
        people_rows.append({
            "sw_id": sw_id,
            "nm": rec.get("nm"),
            "skills": carried,
            "skill_count": len(carried),
        })
    return skill_rows, people_rows


def run_index(cfg: Config) -> dict[str, Any]:
    """Run one index build end to end."""
    client = SourceWellClient(cfg)
    store = IndexStore(cfg.output_dir)

    extract = read_extract()
    profiles = client.crawl_profiles()

    skill_map = build_skill_map(extract)
    vocabulary = collect_skill_vocabulary(client, extract)
    skill_rows, people_rows = build_index_rows(vocabulary, skill_map, profiles)

    counts = {
        "skills_live": sum(1 for r in skill_rows if r.get("status") == "live"),
        "skills_retired": sum(1 for r in skill_rows if r.get("status") == "retired"),
        "skills_without_members": sum(1 for r in skill_rows if not r.get("members")),
        "people_published": len(people_rows),
    }

    store.write_index(skill_rows, people_rows, counts)
    store.write_skills_report(skill_rows)
    return {
        "extract_records": len(extract.get("records") or []),
        "skills": len(skill_rows),
        "people": len(people_rows),
        "counts": counts,
        "vendor_requests": client.request_count,
    }
