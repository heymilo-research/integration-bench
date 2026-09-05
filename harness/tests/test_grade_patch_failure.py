"""grade_once's unapplyable-patch path never touches docker (docs/SPEC.md §4:
"unapplyable patch = unresolved verdict, not a crash") — this is exercised for
real here, only requiring git."""

from pathlib import Path

from bench.commands.grading_core import grade_once

SAMPLE_TASK = Path(__file__).parent / "fixtures" / "sample_task"


def test_unapplyable_patch_yields_unresolved_verdict_not_a_crash(tmp_path):
    garbage_patch = tmp_path / "garbage.patch"
    garbage_patch.write_text(
        "--- a/does_not_exist.py\n+++ b/does_not_exist.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"
    )
    result = grade_once(
        SAMPLE_TASK,
        garbage_patch,
        run_id="test-unapplyable",
        output_root=tmp_path / "artifacts",
    )
    assert result.verdict.resolved is False
    assert result.verdict.error is not None
    assert "did not apply" in result.verdict.error
    assert result.stack is None  # never got to compose


def test_missing_patch_file_yields_unresolved_verdict(tmp_path):
    result = grade_once(
        SAMPLE_TASK,
        tmp_path / "nonexistent.patch",
        run_id="test-missing-patch",
        output_root=tmp_path / "artifacts",
    )
    assert result.verdict.resolved is False
    assert result.verdict.error is not None
