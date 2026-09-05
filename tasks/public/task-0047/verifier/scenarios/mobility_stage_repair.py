"""Acceptance scenario for the bounded GlobalHire mobility stage repair."""

from __future__ import annotations

import csv
import json
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

from bench.verifier.builtin_l2 import builtin_l2


VENDOR = "globalhire"
TOP_FIELDS = [
    "status",
    "source_rows",
    "case_count",
    "updated_count",
    "unchanged_count",
    "rejected_count",
    "cases",
]


def _load_artifacts(output_dir: Path) -> tuple[dict[str, Any], list[str] | None, list[dict[str, str]]]:
    document: dict[str, Any] = {}
    try:
        raw = json.loads((output_dir / "reconciliation.json").read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            document = raw
    except (OSError, ValueError):
        pass

    fields: list[str] | None = None
    rows: list[dict[str, str]] = []
    try:
        with (output_dir / "reconciliation.csv").open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = reader.fieldnames
            rows = list(reader)
    except (OSError, csv.Error):
        pass
    return document, fields, rows


def _normalized_csv(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = dict(row)
        for field in ("source_line", "duplicate_count"):
            try:
                item[field] = int(item[field])
            except (KeyError, TypeError, ValueError):
                item[field] = None
        normalized.append(item)
    return normalized


def _check_report(
    ctx,
    prefix: str,
    document: dict[str, Any],
    csv_fields: list[str] | None,
    csv_rows: list[dict[str, str]],
    expected_cases: list[dict[str, Any]],
    expected_counts: dict[str, int],
    key: dict[str, Any],
) -> None:
    cases = document.get("cases") if isinstance(document.get("cases"), list) else []
    typed_cases = [row for row in cases if isinstance(row, dict)]
    exact_top = (
        set(document) == set(TOP_FIELDS)
        and document.get("status") == "complete"
        and type(document.get("source_rows")) is int
        and type(document.get("case_count")) is int
        and all(type(document.get(f"{name}_count")) is int for name in expected_counts)
    )
    ctx.check_l1(
        f"{prefix}_json_has_exact_document_shape",
        exact_top,
        f"keys={list(document)} status={document.get('status')!r}",
    )
    ctx.check_l1(
        f"{prefix}_json_accounts_for_the_entire_queue",
        document.get("source_rows") == key["source_rows"]
        and document.get("case_count") == key["case_count"]
        and len(typed_cases) == key["case_count"]
        and len(typed_cases) == len(cases),
        f"source_rows={document.get('source_rows')!r} case_count={document.get('case_count')!r} rows={len(typed_cases)}",
    )
    ctx.check_l1(
        f"{prefix}_json_summary_counts_are_exact",
        all(document.get(f"{name}_count") == count for name, count in expected_counts.items()),
        "observed=" + repr({name: document.get(f"{name}_count") for name in expected_counts})
        + f" expected={expected_counts}",
    )
    ctx.check_l1(
        f"{prefix}_json_case_schema_and_order_are_exact",
        len(typed_cases) == len(expected_cases)
        and all(list(row) == key["fields"] for row in typed_cases),
        f"rows={len(typed_cases)} expected={len(expected_cases)}",
    )
    ctx.check_l1(
        f"{prefix}_csv_has_exact_schema_and_cardinality",
        csv_fields == key["fields"] and len(csv_rows) == key["case_count"],
        f"fields={csv_fields!r} rows={len(csv_rows)}",
    )
    csv_normalized = _normalized_csv(csv_rows)
    ctx.check_l1(
        f"{prefix}_csv_and_json_describe_the_same_cases",
        csv_normalized == typed_cases,
        f"csv_rows={len(csv_normalized)} json_rows={len(typed_cases)}",
    )
    for index, expected in enumerate(expected_cases):
        observed = typed_cases[index] if index < len(typed_cases) else None
        ctx.check_l1(
            f"{prefix}_{expected['case_ref'].lower().replace('-', '_')}_is_exact",
            observed == expected,
            f"observed={observed!r} expected={expected!r}",
        )


def _record_gets(log: list[dict[str, Any]], collection: str) -> list[dict[str, Any]]:
    prefix = f"/v1/{collection}/"
    return [
        row
        for row in log
        if row.get("method") == "GET"
        and str(row.get("path", "")).startswith(prefix)
        and str(row.get("path", "")).count("/") == 3
    ]


def _batch_posts(log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in log
        if row.get("method") == "POST"
        and row.get("path") == "/v1/candidates/status-batch"
    ]


def _updates(row: dict[str, Any]) -> list[dict[str, Any]]:
    body = row.get("body")
    updates = body.get("updates") if isinstance(body, dict) else None
    return [item for item in updates if isinstance(item, dict)] if isinstance(updates, list) else []


def _check_read_traffic(ctx, prefix: str, log: list[dict[str, Any]], key: dict[str, Any]) -> None:
    collection_gets = [
        row
        for row in log
        if row.get("method") == "GET"
        and row.get("path") in {"/v1/candidates", "/v1/placements", "/v1/agencies"}
    ]
    ctx.check_l1(
        f"{prefix}_used_bounded_record_reads_only",
        not collection_gets,
        f"collection GETs={[(row.get('path'), row.get('query')) for row in collection_gets]!r}",
    )

    by_collection = {
        name: _record_gets(log, name) for name in ("candidates", "placements", "agencies")
    }
    for name, expected_count in key["expected_read_counts"].items():
        rows = by_collection[name]
        paths = [str(row.get("path")) for row in rows]
        ctx.check_l1(
            f"{prefix}_{name}_were_read_once_per_distinct_id",
            len(rows) == expected_count
            and len(paths) == len(set(paths))
            and all(int(row.get("status") or 0) in ({200, 404} if name == "candidates" else {200}) for row in rows),
            f"requests={len(rows)} unique_paths={len(set(paths))} expected={expected_count}",
        )

    candidate_rows = by_collection["candidates"]
    ctx.check_l1(
        f"{prefix}_every_candidate_authority_read_was_revalidated",
        bool(candidate_rows)
        and all("no-cache" in str((row.get("headers") or {}).get("cache-control", "")).lower() for row in candidate_rows),
        f"revalidated={sum('no-cache' in str((row.get('headers') or {}).get('cache-control', '')).lower() for row in candidate_rows)}/{len(candidate_rows)}",
    )

    paths = {str(row.get("path")) for row in log}
    ctx.check_l1(
        f"{prefix}_conflicting_reference_caused_no_vendor_reads",
        all(not any(path.endswith(record_id) for path in paths) for record_id in key["conflict_only_ids"]),
        f"conflict paths present={sorted(path for path in paths if any(path.endswith(record_id) for record_id in key['conflict_only_ids']))}",
    )
    ctx.check_l1(
        f"{prefix}_missing_candidate_short_circuited_relationship_reads",
        "/v1/placements/plc_00124" not in paths,
        f"placement lookup present={'/v1/placements/plc_00124' in paths}",
    )


def _read_current_stages(ctx, candidate_ids: list[str]) -> dict[str, str]:
    base = ctx.vendor(VENDOR).base_url
    key = ctx.secrets.get("GH_API_KEY", "")
    stages: dict[str, str] = {}
    for candidate_id in candidate_ids:
        url = f"{base}/v1/candidates/{urllib.parse.quote(candidate_id, safe='')}"
        request = urllib.request.Request(
            url,
            headers={"X-GH-Key": key, "Cache-Control": "no-cache"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = json.load(response)
            if isinstance(body, dict):
                stages[candidate_id] = str(body.get("pipeline_stage") or "")
        except Exception:
            stages[candidate_id] = ""
    return stages


def _check_stage_truth(ctx, prefix: str, observed: dict[str, str], expected: dict[str, str]) -> None:
    for candidate_id, stage in expected.items():
        ctx.check_l1(
            f"{prefix}_{candidate_id}_is_{stage}",
            observed.get(candidate_id) == stage,
            f"GlobalHire has {observed.get(candidate_id)!r}, expected {stage!r}",
        )


async def run(ctx) -> None:
    key = json.loads((Path(ctx.fixtures) / "answer_key.json").read_text(encoding="utf-8"))
    vendor = ctx.vendor(VENDOR)
    vendor.recreate(
        checkpoint=key["checkpoint"],
        env={
            "FAULT_CACHE_CONTROL_STALE_SERVE": "1",
            "FAULT_PAYLOAD_413_SPLIT_WRITES": "1",
        },
    )

    before_first = len(vendor.request_log())
    code, _out, err = ctx.app.run()
    first_document, first_csv_fields, first_csv_rows = _load_artifacts(Path(ctx.output_dir))
    ctx.check_l1(
        "first_run_completed_with_both_artifacts",
        code == 0 and bool(first_document) and first_csv_fields is not None,
        f"exit={code} json={bool(first_document)} csv={first_csv_fields is not None} stderr={err[:400]}",
    )
    _check_report(
        ctx,
        "first",
        first_document,
        first_csv_fields,
        first_csv_rows,
        key["first_cases"],
        key["first_counts"],
        key,
    )

    after_first_log = vendor.request_log()
    first_log = after_first_log[before_first:]
    n_after_first = len(after_first_log)
    _check_read_traffic(ctx, "first", first_log, key)

    posts = _batch_posts(first_log)
    attempts = [
        {"status": int(row.get("status") or 0), "size": len(_updates(row))}
        for row in posts
    ]
    ctx.check_l1(
        "first_batch_negotiated_the_live_capacity_without_losing_the_tail",
        attempts == key["first_batch_attempts"],
        f"observed={attempts!r} expected={key['first_batch_attempts']!r}",
    )
    successful_updates = [
        update
        for row in posts
        if int(row.get("status") or 0) == 200
        for update in _updates(row)
    ]
    expected_by_id = {
        row["candidate_id"]: row["requested_stage"]
        for row in key["first_cases"]
        if row["outcome"] == "updated"
    }
    ctx.check_l1(
        "first_successful_batches_cover_each_eligible_candidate_exactly_once",
        Counter(str(row.get("candidate_id")) for row in successful_updates)
        == Counter(key["eligible_candidate_ids"])
        and all(expected_by_id.get(str(row.get("candidate_id"))) == row.get("pipeline_stage") for row in successful_updates),
        f"accepted_ids={[row.get('candidate_id') for row in successful_updates]!r}",
    )
    ctx.check_l1(
        "first_rejected_cases_never_entered_an_accepted_batch",
        all(str(row.get("candidate_id")) in expected_by_id for row in successful_updates),
        f"accepted_ids={[row.get('candidate_id') for row in successful_updates]!r}",
    )

    first_stages = _read_current_stages(ctx, list(key["final_stages"]))
    n_after_probe_first = len(vendor.request_log())
    _check_stage_truth(ctx, "after_first", first_stages, key["final_stages"])

    before_second = len(vendor.request_log())
    code, _out, err = ctx.app.run()
    second_document, second_csv_fields, second_csv_rows = _load_artifacts(Path(ctx.output_dir))
    ctx.check_l1(
        "second_run_completed_with_both_artifacts",
        code == 0 and bool(second_document) and second_csv_fields is not None,
        f"exit={code} json={bool(second_document)} csv={second_csv_fields is not None} stderr={err[:400]}",
    )
    _check_report(
        ctx,
        "second",
        second_document,
        second_csv_fields,
        second_csv_rows,
        key["second_cases"],
        key["second_counts"],
        key,
    )

    after_second_log = vendor.request_log()
    second_log = after_second_log[before_second:]
    n_after_second = len(after_second_log)
    _check_read_traffic(ctx, "second", second_log, key)
    second_posts = _batch_posts(second_log)
    ctx.check_l1(
        "second_run_sent_no_stage_update_requests",
        not second_posts,
        f"stage batch requests={len(second_posts)}",
    )

    second_stages = _read_current_stages(ctx, list(key["final_stages"]))
    n_after_probe_second = len(vendor.request_log())
    _check_stage_truth(ctx, "after_second", second_stages, key["final_stages"])

    await builtin_l2(
        ctx,
        exclude_request_indices=[
            *range(n_after_first, n_after_probe_first),
            *range(n_after_second, n_after_probe_second),
        ],
        app_runs=2,
    )
