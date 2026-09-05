"""Direct-provider tool calls stay inside the agent container boundary."""

from __future__ import annotations

import subprocess

from bench.commands.eval_core import _run_tool
from bench.eval_output import EvalDir


def test_write_file_executes_in_agent_container(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ed = EvalDir(root=tmp_path / "eval", eval_id="direct-tools")
    ed.root.mkdir()
    calls = []

    class Stack:
        def agent_exec(self, *args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    result, done = _run_tool(
        "write_file",
        {"path": "repo/client.py", "content": "VALUE = 1\n"},
        stack=Stack(),
        workspace=workspace,
        ed=ed,
    )

    assert done is False
    assert result == "wrote repo/client.py (10 bytes)"
    assert calls[0][0][0:2] == ("python3", "-c")
    assert calls[0][0][-2:] == ("repo/client.py", "VALUE = 1\n")
    assert calls[0][1] == {"check": False}
    assert not (workspace / "repo/client.py").exists()
