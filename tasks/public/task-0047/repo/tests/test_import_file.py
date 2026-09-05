from pathlib import Path

from globalhire_mobility.import_file import read_actions


def test_read_actions_preserves_lines_and_normalizes_cells(tmp_path: Path) -> None:
    path = tmp_path / "actions.csv"
    path.write_text(
        "case_ref,candidate_id,placement_id,agency_id,requested_stage\n"
        "  CASE-1 , cand_1 , plc_1 , agy_1 , screening \n",
        encoding="utf-8",
    )
    rows = read_actions(path)
    assert len(rows) == 1
    assert rows[0].source_line == 2
    assert rows[0].case_ref == "CASE-1"
    assert rows[0].values() == ("cand_1", "plc_1", "agy_1", "screening")
