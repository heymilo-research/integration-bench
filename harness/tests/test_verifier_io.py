import json
import threading

from bench.verifier.io import read_json_output


def test_reads_valid_file_immediately(tmp_path):
    p = tmp_path / "out.json"
    p.write_text(json.dumps([{"a": 1}]), encoding="utf-8")
    assert read_json_output(p, timeout_s=1.0) == [{"a": 1}]


def test_returns_none_when_file_never_appears(tmp_path):
    assert read_json_output(tmp_path / "missing.json", timeout_s=0.4, poll_s=0.1) is None


def test_waits_out_truncated_then_completed_write(tmp_path):
    p = tmp_path / "out.json"
    p.write_text('[{"a": 1}', encoding="utf-8")  # truncated: does not parse

    def complete():
        p.write_text('[{"a": 1}]', encoding="utf-8")

    t = threading.Timer(0.3, complete)
    t.start()
    try:
        assert read_json_output(p, timeout_s=3.0, poll_s=0.05) == [{"a": 1}]
    finally:
        t.cancel()
