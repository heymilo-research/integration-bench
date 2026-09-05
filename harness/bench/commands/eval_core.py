"""Public ``bench eval``: Compose unit + provider LLM + grade (no Prime)."""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from bench.commands.grading_core import grade_once
from bench.compose import ParticipantResourceError
from bench.compose_unit import (
    ComposeUnitStack,
    codex_auth_mode,
    make_path_host_owned,
    stage_workspace_into_eval,
)
from bench.config import load_task_config
from bench.eval_output import DEFAULT_OUTPUT_ROOT, EvalDir
from bench.providers import (
    ProviderError,
    chat,
    load_dotenv_files,
    require_api_key,
    resolve_provider,
)
from bench.provenance import capture_image_identity, capture_provenance
from bench.scoring import SCORER_VERSION
from bench.workspace import extract_diff


@dataclasses.dataclass
class EvalResult:
    eval_id: str
    task_id: str
    model: str
    provider: str
    turns: int
    timed_out: bool
    output_dir: Path
    patch_path: Path | None
    verdict_path: Path | None
    resolved: bool
    reward: float | None
    error: str | None
    # Why the rollout stopped. "done" (agent called done), "turn_cap",
    # "wall_clock", "provider_error", "usage_limit". A rollout cut off by
    # turn_cap or wall_clock was TRUNCATED, not beaten: its reward is a lower
    # bound on the model, and binary scoring punishes it as a whole-task failure.
    # Reporting it as a plain low score is how a too-small budget gets published
    # as a model result.
    #
    # "usage_limit" is the same hazard with a worse failure mode: the
    # subscription window ran out mid-run, so the diff on disk is whatever the
    # agent had written when it was cut off. Such a rollout is NOT graded at all
    # (reward stays None) — see eval_claude_code_once. It must be re-run, never
    # averaged.
    stop_reason: str = "done"
    raw_score: int | None = None
    task_score: float | None = None
    check_coverage: float | None = None
    missing_checks: list[str] = dataclasses.field(default_factory=list)
    failure_class: str = "candidate_result"


_SYSTEM = """You are a software engineer solving an Integration-Bench task.
Work only inside /workspace (PROBLEM.md, repo/, and optional materials/).
Vendor APIs and their canonical documentation are reachable via *_BASE_URL and
*_DOCS_URL environment variables.

Tools:
- bash: run a shell command (cwd /workspace)
- read_file / write_file: paths relative to /workspace
- done: call when ready to grade

Read PROBLEM.md, canonical vendor docs, and optional materials/ first. Implement the solution under repo/.
Call done when finished.
"""


def rollout_failure_class(stop_reason: str, grade_failure_class: str) -> str:
    if stop_reason in ("provider_error", "usage_limit"):
        return "provider_infrastructure_failure"
    if stop_reason in ("turn_cap", "wall_clock", "resource_limit"):
        return "candidate_runtime_failure"
    return grade_failure_class


def _remove_ephemeral_agent_home(ed: EvalDir, *, agent_image: str | None = None) -> None:
    """Remove copied auth and all CLI-created state from a completed rollout.

    Transcripts, logs, patches, provenance, and verdicts live elsewhere under
    the eval directory and are retained. This directory contains credentials,
    sessions, project history, caches, and shell snapshots and must not become
    evaluation evidence.
    """
    agent_home = ed.root / "agent-home"
    provider_auth = ed.root / "provider-auth"
    try:
        if agent_home.exists():
            if agent_image:
                make_path_host_owned(agent_home, agent_image)
            shutil.rmtree(agent_home)
    finally:
        # This host-owned gateway input must disappear even if ownership repair
        # of participant-controlled files fails.
        if provider_auth.exists():
            shutil.rmtree(provider_auth)


def _cleanup_ephemeral_agent_home(ed: EvalDir, stack: ComposeUnitStack) -> None:
    """Return rollout paths to the host and securely remove copied secrets.

    Both paths are assigned to the image's numeric agent uid on native Linux.
    The host must own the workspace again before Git can extract the candidate
    diff; otherwise Git rejects the repository as dubious ownership even though
    the model completed successfully.
    """
    _return_workspace_to_host(ed, stack)
    try:
        _remove_ephemeral_agent_home(ed, agent_image=stack._agent_image)
    except Exception as exc:
        ed.append_agent_log(f"warning: could not remove ephemeral agent home: {exc}\n")


def _return_workspace_to_host(ed: EvalDir, stack: ComposeUnitStack) -> None:
    """Restore host ownership before Git reads or mutates the candidate repo."""
    try:
        make_path_host_owned(ed.workspace, stack._agent_image)
    except Exception as exc:
        ed.append_agent_log(f"warning: could not return workspace to host: {exc}\n")
    ed.scrub_runtime_secrets(ed.workspace)


def _extract_candidate_diff(repo_dir: Path, ed: EvalDir) -> str:
    """Never retain or grade a patch produced by credential exfiltration."""
    if ed.secret_leak_detected:
        ed.append_agent_log(
            "runtime secret exfiltration detected; retained workspace was redacted "
            "and the candidate patch was rejected\n"
        )
        return ""
    return extract_diff(repo_dir)


def _security_outcome(
    ed: EvalDir,
    *,
    resolved: bool,
    reward: float | None,
    error: str | None,
    failure_class: str,
) -> tuple[bool, float | None, str | None, str]:
    if not ed.secret_leak_detected:
        return resolved, reward, error, failure_class
    return (
        False,
        None,
        "runtime secret exfiltration detected and redacted",
        "candidate_runtime_failure",
    )


def _run_tool(
    name: str,
    args: dict[str, Any],
    *,
    stack: ComposeUnitStack,
    workspace: Path,
    ed: EvalDir,
) -> tuple[str, bool]:
    if name == "done":
        return "ok", True
    if name == "bash":
        cmd = str(args.get("command") or "")
        ed.append_agent_log(f"$ {cmd}\n")
        result = stack.agent_exec("bash", "-lc", cmd, check=False)
        out = (result.stdout or "")[-8000:]
        err = (result.stderr or "")[-4000:]
        text = f"exit={result.returncode}\nstdout:\n{out}\nstderr:\n{err}"
        ed.append_agent_log(text + "\n")
        return text, False
    if name == "read_file":
        rel = str(args.get("path") or "")
        # Host bind-mount = agent /workspace
        path = (workspace / rel).resolve()
        try:
            path.relative_to(workspace.resolve())
        except ValueError:
            return f"ERROR: path escapes workspace: {rel}", False
        if not path.is_file():
            return f"ERROR: not a file: {rel}", False
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > 100_000:
            text = text[:100_000] + "\n…[truncated]"
        return text, False
    if name == "write_file":
        rel = str(args.get("path") or "")
        path = (workspace / rel).resolve()
        try:
            path.relative_to(workspace.resolve())
        except ValueError:
            return f"ERROR: path escapes workspace: {rel}", False
        content = str(args.get("content") or "")
        if len(content.encode("utf-8")) > 1_000_000:
            return "ERROR: write_file content exceeds 1 MiB", False
        result = stack.agent_exec(
            "python3",
            "-c",
            (
                "from pathlib import Path; import sys; "
                "p=Path('/workspace')/sys.argv[1]; "
                "p.parent.mkdir(parents=True, exist_ok=True); "
                "p.write_text(sys.argv[2], encoding='utf-8')"
            ),
            rel,
            content,
            check=False,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout or "write failed").strip()
            return f"ERROR: {detail[-2000:]}", False
        msg = f"wrote {rel} ({len(content.encode('utf-8'))} bytes)"
        ed.append_agent_log(msg + "\n")
        return msg, False
    return f"ERROR: unknown tool {name}", False


def eval_once(
    task_dir: Path,
    model: str,
    *,
    max_turns: int = 40,
    keep: bool = False,
    output_root: Path | None = None,
    eval_id: str | None = None,
    logical_rollout_id: str | None = None,
) -> EvalResult:
    load_dotenv_files()
    task_dir = Path(task_dir)
    task = load_task_config(task_dir)
    provider, model_id = resolve_provider(model)
    # Preflight the credential BEFORE creating an eval dir or starting Docker.
    # require_api_key used to be reached only inside the turn loop, so a missing
    # key spun up the whole stack and then produced a COMPLETE-LOOKING verdict
    # with reward 0.0 and turns=1. The exit code was correct (1), but a sweep
    # driver that collects rewards from verdict files rather than exit codes
    # would record a legitimate-looking zero for every task and report that the
    # model scored nothing. Fail before anything is written.
    require_api_key(provider)

    ed = EvalDir.create(
        output_root=Path(output_root or DEFAULT_OUTPUT_ROOT),
        eval_id=eval_id,
        logical_rollout_id=logical_rollout_id,
    )
    provenance = capture_provenance(task_dir, model=model_id, provider=provider.name, mode="direct")
    ed.write_meta(
        {
            "status": "running",
            "task": task.id,
            "model": model_id,
            "provider": provider.name,
            "harness": "direct",
            "mode": "direct",
            "provenance": provenance,
        }
    )
    repo_dir = stage_workspace_into_eval(task_dir, ed)
    workspace = ed.workspace

    stack = ComposeUnitStack(
        task_dir,
        ed,
        app_repo=repo_dir,
        include_agent=True,
    )
    provenance["agent_image"] = capture_image_identity(stack._agent_image)
    ed.write_meta({"provenance": provenance})
    # up() renders the Compose file. Rendering here as well provisions
    # one-shot agent homes twice and breaks the subscription-backed lanes.
    stack.up(exclude_app=True)

    # Export vendor URLs into agent env already via compose; also for host logs.
    problem = ""
    problem_path = workspace / "PROBLEM.md"
    if problem_path.is_file():
        problem = problem_path.read_text(encoding="utf-8", errors="replace")

    user0 = (
        f"Task {task.id}. Workspace is /workspace.\n\n"
        f"PROBLEM.md:\n{problem[:20000]}\n\n"
        "Vendor credentials and base URLs are already in the environment."
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": user0}]
    ed.append_transcript({"type": "user", "content": user0[:4000]})

    timed_out = False
    error: str | None = None
    turns = 0
    done = False
    started = time.time()
    usage_total: dict[str, int] = {}
    # Wall clock is the real budget and it is a per-task property; max_turns is a
    # runaway-loop backstop. eval_once previously enforced only the turn cap and
    # used `started` for reporting alone, so the provider lane ignored the
    # timeout_minutes that rollout_core and eval_claude_code_once both honour.
    deadline = started + int(task.timeout_minutes * 60)
    stop_reason = "turn_cap"

    try:
        for turn in range(max_turns):
            if time.time() >= deadline:
                timed_out = True
                stop_reason = "wall_clock"
                ed.append_transcript(
                    {
                        "type": "error",
                        "error": (
                            f"wall-clock budget of {task.timeout_minutes} min exhausted "
                            f"after {turns} turn(s); rollout truncated"
                        ),
                    }
                )
                break
            turns = turn + 1
            try:
                assistant = chat(
                    provider=provider,
                    model=model_id,
                    system=_SYSTEM,
                    messages=messages,
                )
            except ProviderError as exc:
                error = str(exc)
                stop_reason = "provider_error"
                ed.append_transcript({"type": "error", "error": error})
                break

            for _k, _v in (assistant.usage or {}).items():
                usage_total[_k] = usage_total.get(_k, 0) + _v

            asst_msg: dict[str, Any] = {
                "role": "assistant",
                "content": assistant.content or "",
            }
            if assistant.tool_calls:
                asst_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in assistant.tool_calls
                ]
            messages.append(asst_msg)
            ed.append_transcript(
                {
                    "type": "assistant",
                    "turn": turns,
                    "content": (assistant.content or "")[:4000],
                    "tool_calls": [
                        {"name": tc.name, "arguments": tc.arguments} for tc in assistant.tool_calls
                    ],
                }
            )

            if not assistant.tool_calls:
                messages.append(
                    {
                        "role": "user",
                        "content": "Continue with tools (bash/read_file/write_file) or call done.",
                    }
                )
                continue

            for tc in assistant.tool_calls:
                try:
                    result_text, done = _run_tool(
                        tc.name,
                        tc.arguments,
                        stack=stack,
                        workspace=workspace,
                        ed=ed,
                    )
                except ParticipantResourceError as exc:
                    error = str(exc)
                    stop_reason = "resource_limit"
                    result_text = f"ERROR: {error}"
                    done = True
                    ed.append_agent_log(result_text + "\n")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_text,
                    }
                )
                ed.append_transcript(
                    {
                        "type": "tool_result",
                        "turn": turns,
                        "name": tc.name,
                        "content": result_text[:4000],
                    }
                )
                if done:
                    if stop_reason != "resource_limit":
                        stop_reason = "done"
                    break
            if done:
                break
        else:
            # Loop ran to exhaustion: the turn cap, not the agent, ended this.
            timed_out = True
            stop_reason = "turn_cap"
    finally:
        try:
            ed.write_vendor_log(stack.logs("vendors"))
        except Exception:
            pass
        # Keep compose up for grade in same project; tear down after grade unless keep.
        if not keep:
            # Grade will start its own stack on same eval_dir after we down here
            # OR we down after grade. Down now so grade gets a clean vendors boot.
            try:
                stack.down()
            except Exception:
                pass
        _return_workspace_to_host(ed, stack)

    patch_text = _extract_candidate_diff(repo_dir, ed)
    ed.write_patch(patch_text)

    grade = grade_once(
        task_dir,
        ed.patch_path,
        run_id=ed.eval_id,
        keep=keep,
        eval_dir=ed,
    )

    if not keep:
        # grade_once already downs its stack
        pass

    verdict = grade.verdict
    failure_class = rollout_failure_class(stop_reason, grade.failure_class)
    reward = (
        None
        if grade.task_score is None or failure_class == "provider_infrastructure_failure"
        else grade.task_score / 100.0
    )
    result_resolved, reward, result_error, failure_class = _security_outcome(
        ed,
        resolved=grade.solved,
        reward=reward,
        error=error or verdict.error,
        failure_class=failure_class,
    )

    ed.write_meta(
        {
            "status": "done",
            "turns": turns,
            "timed_out": timed_out,
            "resolved": result_resolved,
            "legacy_resolved": verdict.resolved,
            "reward": reward,
            "reward_semantics": "task_score_div_100",
            "scorer_version": SCORER_VERSION,
            "raw_score": grade.raw_score,
            "task_score": grade.task_score,
            "check_coverage": grade.check_coverage,
            "missing_checks": grade.missing_checks,
            "failure_class": failure_class,
            "elapsed_s": time.time() - started,
            "usage": usage_total,
            "error": result_error,
            # Persisted so a sweep driver reading meta.json (rather than our
            # return value) can tell a truncated rollout from a real low score.
            "stop_reason": stop_reason,
            "truncated": stop_reason in ("turn_cap", "wall_clock"),
            "max_turns": max_turns,
            "timeout_minutes": task.timeout_minutes,
        }
    )

    return EvalResult(
        eval_id=ed.eval_id,
        task_id=task.id,
        model=model_id,
        provider=provider.name,
        turns=turns,
        timed_out=timed_out,
        output_dir=ed.root,
        patch_path=ed.patch_path,
        verdict_path=ed.verdict_path if ed.verdict_path.is_file() else None,
        resolved=result_resolved,
        reward=reward,
        error=result_error,
        stop_reason=stop_reason,
        raw_score=grade.raw_score,
        task_score=grade.task_score,
        check_coverage=grade.check_coverage,
        missing_checks=grade.missing_checks,
        failure_class=failure_class,
    )


_CC_PROMPT = """Task {task_id}. Your working directory is /workspace and it \
contains PROBLEM.md, repo/, and optional task-specific materials/.

Implement the solution by editing files under repo/ ONLY. Do not modify or \
create anything outside /workspace.

The vendor APIs and canonical vendor documentation are reachable from this \
environment: URLs are in *_BASE_URL and *_DOCS_URL environment variables \
(start with VENDOR_BASE_URL and VENDOR_DOCS_URL), and credentials are already set.

Read PROBLEM.md, canonical vendor docs, and materials/ when present, then implement the change. \
When you are finished, stop.

PROBLEM.md:
{problem}
"""


def _cc_brief(value: Any, limit: int = 200) -> str:
    """One-line summary of a tool input, preferring its most telling field."""
    if isinstance(value, dict):
        for key in ("command", "file_path", "path", "pattern", "description"):
            if key in value:
                return f"{key}={str(value[key])[:limit]}"
        return json.dumps(value)[:limit]
    return str(value)[:limit]


def _cc_event_lines(event: dict[str, Any]) -> list[str]:
    """Render one ``stream-json`` event as human-readable progress lines."""
    etype = event.get("type")
    if etype == "system":
        if event.get("subtype") == "init":
            return [f"[init] model={event.get('model')} cwd={event.get('cwd')}"]
        return [f"[system] {event.get('subtype') or ''}".rstrip()]
    if etype in ("assistant", "user"):
        lines: list[str] = []
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = (block.get("text") or "").strip()
                if text:
                    lines.append(f"[assistant] {text}")
            elif btype == "tool_use":
                lines.append(f"[tool] {block.get('name')} {_cc_brief(block.get('input'))}")
            elif btype == "tool_result":
                content = block.get("content")
                if isinstance(content, list):
                    content = " ".join(
                        str(c.get("text", "")) for c in content if isinstance(c, dict)
                    )
                text = str(content or "").strip().replace("\n", " ")
                lines.append(f"[result] {text[:300]}")
        return lines
    if etype == "result":
        return [
            f"[done] subtype={event.get('subtype')} turns={event.get('num_turns')} "
            f"duration_ms={event.get('duration_ms')} is_error={event.get('is_error')}"
        ]
    return []


# Claude Code reports an exhausted subscription window as a plain, non-JSON
# stdout line ("Claude AI usage limit reached|<epoch>") and/or a terminal
# `result` event carrying is_error. Only those two channels are scanned.
#
# DO NOT widen this to /rate.?limit/ or /\b429\b/. Integration-Bench is a
# benchmark ABOUT rate limiting: the agent prints "429" and "rate limit"
# constantly while solving task-0022, task-0047 and the rest, and every one of
# those strings arrives inside an assistant message. A broad match would label
# successful runs on exactly the tasks we care most about as `usage_limit` and
# silently drop them from the results — inverting the bug this exists to fix.
# Match the CLI's own wording, in the CLI's own channels, and nothing else.
_LIMIT_TEXT = re.compile(
    r"usage limit reached|usage limit will reset|approaching your usage limit|"
    r"(?:you['’]ve|you have) hit your session limit",
    re.I,
)
# The CLI appends the reset time as an epoch after a pipe.
_LIMIT_RESET = re.compile(r"usage limit reached\s*\|\s*(\d{9,})", re.I)


def detect_usage_limit(text: str) -> tuple[bool, int | None]:
    """``(hit, reset_epoch_s)`` for one line of Claude Code CLI output.

    Callers must pass only CLI-level output (non-JSON lines, or the text of an
    errored terminal `result` event) — never assistant message content. See the
    comment above _LIMIT_TEXT for why that distinction is load-bearing.
    """
    if not text or not _LIMIT_TEXT.search(text):
        return False, None
    m = _LIMIT_RESET.search(text)
    return True, int(m.group(1)) if m else None


def _codex_item_lines(item: dict[str, Any], *, started: bool) -> list[str]:
    """Render one ``codex exec --json`` item as human-readable progress lines."""
    itype = item.get("type")
    if itype == "agent_message":
        text = (item.get("text") or "").strip()
        return [f"[assistant] {text}"] if text else []
    if itype == "reasoning":
        text = (item.get("text") or "").strip().replace("\n", " ")
        return [f"[reasoning] {text[:300]}"] if text else []
    if itype == "command_execution":
        if started:
            return [f"$ {str(item.get('command') or '')[:400]}"]
        out = str(item.get("aggregated_output") or "").strip().replace("\n", " ")
        return [f"[result] exit={item.get('exit_code')} {out[:300]}"]
    if itype == "file_change":
        # Only on completion. Codex emits file_change as a started/completed
        # pair carrying the same paths, so rendering both duplicated every edit
        # line in the agent log (observed on the 2026-08-10 container canary).
        # command_execution is the deliberate exception: its pair is `$ cmd`
        # then `[result]`, which is two DIFFERENT lines.
        if started:
            return []
        changes = item.get("changes") or []
        paths = [str(c.get("path")) for c in changes if isinstance(c, dict) and c.get("path")]
        return [f"[edit] {', '.join(paths)[:400]}"] if paths else ["[edit]"]
    if itype == "mcp_tool_call":
        return [f"[mcp] {item.get('server')}.{item.get('tool')}"]
    if itype == "web_search":
        return [f"[search] {str(item.get('query') or '')[:200]}"]
    if itype == "todo_list":
        return [f"[plan] {len(item.get('items') or [])} item(s)"]
    return []


def _codex_event_lines(event: dict[str, Any]) -> list[str]:
    """Render one ``codex exec --json`` JSONL event as progress lines.

    Sibling of :func:`_cc_event_lines`. The two CLIs stream different schemas —
    Claude Code emits Anthropic message envelopes, Codex emits
    ``thread``/``turn``/``item`` events — so the rendering cannot be shared, but
    the resulting agent log is deliberately kept in the same shape.
    """
    etype = event.get("type")
    if etype == "thread.started":
        return [f"[init] thread={event.get('thread_id')}"]
    if etype == "turn.started":
        return []
    if etype in ("item.started", "item.completed"):
        item = event.get("item")
        if not isinstance(item, dict):
            return []
        return _codex_item_lines(item, started=etype == "item.started")
    if etype == "turn.completed":
        usage = event.get("usage") or {}
        return [
            f"[done] in={usage.get('input_tokens')} "
            f"cached={usage.get('cached_input_tokens')} "
            f"out={usage.get('output_tokens')}"
        ]
    if etype == "turn.failed":
        err = event.get("error")
        detail = err.get("message") if isinstance(err, dict) else err
        return [f"[failed] {str(detail or '')[:400]}"]
    return []


# Codex reports an exhausted plan window in its own wording. The SAME rule as
# _LIMIT_TEXT applies and is if anything sharper here: Integration-Bench is a
# benchmark ABOUT rate limiting, and a Codex rollout solving task-0022 or
# task-0047 prints "rate limit", "429" and "retry-after" constantly — inside
# reasoning items, executed curl output, and the connector source it writes.
# Matching any of those would mark successful runs on the tasks we care most
# about as `usage_limit` and drop them from the sweep.
#
# So: match Codex's own plan-exhaustion sentence and its internal marker, and
# nothing else. Verified against the strings in codex-cli 0.146.0.
_CODEX_LIMIT_TEXT = re.compile(
    r"(?:you['’]ve|you have) hit your usage limit|usage_limit_reached",
    re.I,
)
# Codex does not append a reset epoch the way Claude Code does; when a machine
# readable one IS present it arrives as a JSON field, so only that form is read.
_CODEX_LIMIT_RESET = re.compile(r"\"resets_at\"\s*:\s*(\d{9,})")


def detect_codex_usage_limit(text: str) -> tuple[bool, int | None]:
    """``(hit, reset_epoch_s)`` for one line of Codex CLI output.

    Callers must pass only CLI-level output (non-JSON lines, or the error text
    of a `turn.failed` event) — never assistant/reasoning content. See the
    comment above _CODEX_LIMIT_TEXT for why that distinction is load-bearing.
    """
    if not text or not _CODEX_LIMIT_TEXT.search(text):
        return False, None
    m = _CODEX_LIMIT_RESET.search(text)
    return True, int(m.group(1)) if m else None


def eval_claude_code_once(
    task_dir: Path,
    *,
    model: str | None = None,
    keep: bool = False,
    output_root: Path | None = None,
    eval_id: str | None = None,
    logical_rollout_id: str | None = None,
    stream: bool = True,
) -> EvalResult:
    """Run Claude Code (subscription auth) as the agent inside the isolated
    compose-unit agent container, then grade the resulting diff.

    Isolation: the ``claude`` binary runs as the non-root ``agent`` user with
    only ``/workspace`` (+ the mounted Claude config) visible — never the task
    root, verifier, or gold patch. See ``_claude_code_agent_extras``.
    """
    rollout_started = time.time()
    rollout_started_mono = time.monotonic()
    load_dotenv_files()
    task_dir = Path(task_dir)
    task = load_task_config(task_dir)

    ed = EvalDir.create(
        output_root=Path(output_root or DEFAULT_OUTPUT_ROOT),
        eval_id=eval_id,
        logical_rollout_id=logical_rollout_id,
    )
    model_label = model or "claude-code-default"
    provenance = capture_provenance(
        task_dir, model=model_label, provider="claude-code", mode="claude-code"
    )
    ed.write_meta(
        {
            "status": "running",
            "task": task.id,
            "model": model_label,
            "provider": "claude-code",
            "harness": "claude-code",
            "mode": "claude-code",
            "provenance": provenance,
        }
    )
    repo_dir = stage_workspace_into_eval(task_dir, ed)
    workspace = ed.workspace

    stack = ComposeUnitStack(
        task_dir,
        ed,
        app_repo=repo_dir,
        include_agent=True,
        agent_claude_code=True,
    )
    provenance["agent_image"] = capture_image_identity(stack._agent_image)
    ed.write_meta({"provenance": provenance})
    # up() owns Compose rendering and one-shot agent-home provisioning.
    stack.up(exclude_app=True)

    problem = ""
    problem_path = workspace / "PROBLEM.md"
    if problem_path.is_file():
        problem = problem_path.read_text(encoding="utf-8", errors="replace")
    prompt = _CC_PROMPT.format(task_id=task.id, problem=problem[:20000])
    ed.append_transcript({"type": "user", "content": prompt[:4000]})

    timeout_s = int(task.timeout_minutes * 60)
    timed_out = False
    resource_limited = False
    error: str | None = None
    returncode: int | None = None
    started = time.time()
    started_mono = time.monotonic()

    # In-container `timeout` is the hard wall (kills the claude child); the
    # host-side backstop is generous so it only fires if exec itself wedges.
    # `stream-json` (which requires --verbose) is what makes a multi-minute
    # run observable — plain `text` emits nothing until the process exits.
    cc_cmd = [
        "timeout",
        # SIGTERM alone did not kill it: a task-0012 rollout survived the 3600s
        # SIGTERM and ran 12,450s (2026-08-09). --kill-after escalates to SIGKILL
        # 60s later, which nothing can trap.
        "--kill-after=60",
        str(timeout_s),
        "claude",
        "-p",
        prompt,
        "--dangerously-skip-permissions",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    if model:
        cc_cmd += ["--model", model]
    effort = os.environ.get("IB_REASONING_EFFORT", "").strip()
    if effort and effort != "claude-code-model-default":
        cc_cmd += ["--effort", effort]

    usage: dict[str, Any] = {}
    cost_usd: float | None = None
    usage_limit = False
    limit_reset_epoch: int | None = None
    provider_failure = False
    provider_failure_detail: str | None = None

    def note_limit(text: str) -> None:
        nonlocal usage_limit, limit_reset_epoch
        hit, reset = detect_usage_limit(text)
        if hit:
            usage_limit = True
            if reset is not None:
                limit_reset_epoch = reset

    def note_provider_failure(text: str) -> None:
        nonlocal provider_failure, provider_failure_detail
        normalized = text.strip().lower()
        labels = {"authentication_failed", "server_error", "api_error"}
        markers = (
            "failed to authenticate",
            "oauth access token has expired",
            "api error: 500 internal server error",
            "server-side issue, usually temporary",
        )
        if normalized in labels or any(marker in normalized for marker in markers):
            provider_failure = True
            if provider_failure_detail is None:
                provider_failure_detail = text.strip()[:500]

    def handle_line(line: str) -> None:
        nonlocal usage, cost_usd, usage_limit, limit_reset_epoch
        if not line.strip():
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # Non-JSON output (auth errors, CLI warnings, the usage-limit
            # notice) is the CLI speaking for itself, so it is safe to scan.
            note_limit(line)
            note_provider_failure(line)
            ed.append_agent_log(line)
            if stream:
                print(line, flush=True)
            return
        ed.append_transcript(event)
        if event.get("error"):
            note_provider_failure(str(event["error"]))
        if event.get("type") == "rate_limit_event":
            info = event.get("rate_limit_info") or {}
            if info.get("status") == "rejected":
                usage_limit = True
                reset = info.get("resetsAt")
                if isinstance(reset, (int, float)):
                    limit_reset_epoch = int(reset)
        if event.get("type") == "result":
            usage = event.get("usage") or {}
            cost_usd = event.get("total_cost_usd")
            # Scan the terminal result ONLY when the CLI flagged it an error.
            # A successful result's `result` field is the agent's own prose,
            # which on this benchmark routinely discusses rate limiting.
            if event.get("is_error") or str(event.get("subtype", "")) not in ("success", ""):
                terminal_error = " ".join(
                    str(event.get(k, "")) for k in ("subtype", "result", "error", "message")
                )
                note_limit(terminal_error)
                note_provider_failure(terminal_error)
        for text in _cc_event_lines(event):
            ed.append_agent_log(text)
            if stream:
                print(text, flush=True)

    header = f"$ claude -p <prompt {len(prompt)} chars> --dangerously-skip-permissions" + (
        f" --model {model}" if model else ""
    )
    ed.append_agent_log(header)
    if stream:
        print(header, flush=True)

    try:
        try:
            returncode = stack.agent_exec_stream(
                *cc_cmd,
                on_line=handle_line,
                timeout_s=timeout_s + 120,
            )
            ed.append_agent_log(f"exit={returncode}")
            # coreutils `timeout` exits 124 when it kills the command.
            if returncode == 124:
                timed_out = True
                error = "claude code exceeded task timeout"
            elif returncode != 0:
                error = f"claude code exited with status {returncode}"
        except subprocess.TimeoutExpired:
            timed_out = True
            error = "claude code exec exceeded host timeout"
            ed.append_agent_log(error + "\n")
        except ParticipantResourceError as exc:
            resource_limited = True
            error = str(exc)
            ed.append_agent_log(error + "\n")
    finally:
        try:
            ed.write_vendor_log(stack.logs("vendors"))
        except Exception:
            pass
        if not keep:
            try:
                stack.down()
            except Exception:
                pass
            _cleanup_ephemeral_agent_home(ed, stack)
        else:
            _return_workspace_to_host(ed, stack)

    agent_finished = time.time()
    agent_finished_mono = time.monotonic()

    patch_text = _extract_candidate_diff(repo_dir, ed)
    ed.write_patch(patch_text)

    # Provider gates can leave a useful-looking partial diff on disk. Grading
    # that diff would make an auth expiry, server 500, or subscription limit
    # indistinguishable from a genuine model failure. Refuse to grade it and
    # leave an explicitly re-runnable infrastructure outcome instead.
    if usage_limit or provider_failure:
        if usage_limit:
            stop_reason = "usage_limit"
            msg = "claude code hit the subscription usage limit"
            if limit_reset_epoch:
                msg += f" (resets at epoch {limit_reset_epoch})"
        else:
            stop_reason = "provider_error"
            msg = "claude code provider failure"
            if provider_failure_detail:
                msg += f": {provider_failure_detail}"
        ed.append_agent_log(msg)
        ed.write_meta(
            {
                "status": stop_reason,
                "timed_out": timed_out,
                "resolved": False,
                "reward": None,
                "reward_semantics": "task_score_div_100",
                "scorer_version": SCORER_VERSION,
                "elapsed_s": time.time() - started,
                "timing": {
                    "setup_s": started_mono - rollout_started_mono,
                    "model_s": agent_finished_mono - started_mono,
                    "grading_s": None,
                    "total_s": agent_finished_mono - rollout_started_mono,
                    "wall_total_s": agent_finished - rollout_started,
                    "machine_sleep_s": max(
                        0.0,
                        (agent_finished - rollout_started)
                        - (agent_finished_mono - rollout_started_mono),
                    ),
                    "provider_queue_s": None,
                    "model_s_includes_provider_queue": True,
                },
                "agent_returncode": returncode,
                "usage": usage,
                "total_cost_usd": cost_usd,
                "error": msg,
                "stop_reason": stop_reason,
                "truncated": True,
                "graded": False,
                "limit_reset_epoch": limit_reset_epoch,
                "timeout_minutes": task.timeout_minutes,
                "failure_class": "provider_infrastructure_failure",
            }
        )
        return EvalResult(
            eval_id=ed.eval_id,
            task_id=task.id,
            model=model_label,
            provider="claude-code",
            turns=1,
            timed_out=timed_out,
            output_dir=ed.root,
            patch_path=ed.patch_path,
            verdict_path=None,
            resolved=False,
            reward=None,
            error=msg,
            stop_reason=stop_reason,
            failure_class="provider_infrastructure_failure",
        )

    grading_started_mono = time.monotonic()
    grade = grade_once(
        task_dir,
        ed.patch_path,
        run_id=ed.eval_id,
        keep=keep,
        eval_dir=ed,
    )
    grading_finished = time.time()
    grading_finished_mono = time.monotonic()
    verdict = grade.verdict
    stop_reason = "resource_limit" if resource_limited else "wall_clock" if timed_out else "done"
    failure_class = rollout_failure_class(stop_reason, grade.failure_class)
    if returncode not in (None, 0) and not timed_out:
        failure_class = "candidate_runtime_failure"
    reward = None if grade.task_score is None else grade.task_score / 100.0
    result_resolved, reward, result_error, failure_class = _security_outcome(
        ed,
        resolved=grade.solved,
        reward=reward,
        error=error or verdict.error,
        failure_class=failure_class,
    )

    ed.write_meta(
        {
            "status": "done",
            "timed_out": timed_out,
            "resolved": result_resolved,
            "legacy_resolved": verdict.resolved,
            "reward": reward,
            "reward_semantics": "task_score_div_100",
            "scorer_version": SCORER_VERSION,
            "raw_score": grade.raw_score,
            "task_score": grade.task_score,
            "check_coverage": grade.check_coverage,
            "missing_checks": grade.missing_checks,
            "failure_class": failure_class,
            "elapsed_s": time.time() - started,
            "timing": {
                "setup_s": started_mono - rollout_started_mono,
                "model_s": agent_finished_mono - started_mono,
                "grading_s": grading_finished_mono - grading_started_mono,
                "total_s": grading_finished_mono - rollout_started_mono,
                "wall_total_s": grading_finished - rollout_started,
                "machine_sleep_s": max(
                    0.0,
                    (grading_finished - rollout_started)
                    - (grading_finished_mono - rollout_started_mono),
                ),
                "provider_queue_s": None,
                "model_s_includes_provider_queue": True,
            },
            "agent_returncode": returncode,
            "usage": usage,
            "total_cost_usd": cost_usd,
            "error": result_error,
            "stop_reason": stop_reason,
            "truncated": bool(timed_out),
            "timeout_minutes": task.timeout_minutes,
        }
    )

    return EvalResult(
        eval_id=ed.eval_id,
        task_id=task.id,
        model=model_label,
        provider="claude-code",
        turns=1,
        timed_out=timed_out,
        output_dir=ed.root,
        patch_path=ed.patch_path,
        verdict_path=ed.verdict_path if ed.verdict_path.is_file() else None,
        resolved=result_resolved,
        reward=reward,
        error=result_error,
        stop_reason=stop_reason,
        raw_score=grade.raw_score,
        task_score=grade.task_score,
        check_coverage=grade.check_coverage,
        missing_checks=grade.missing_checks,
        failure_class=failure_class,
    )


def eval_codex_once(
    task_dir: Path,
    *,
    model: str | None = None,
    keep: bool = False,
    output_root: Path | None = None,
    eval_id: str | None = None,
    logical_rollout_id: str | None = None,
    stream: bool = True,
) -> EvalResult:
    """Run the Codex CLI (ChatGPT-plan auth) as the agent inside the isolated
    compose-unit agent container, then grade the resulting diff.

    Structural sibling of :func:`eval_claude_code_once`. Two deliberate
    differences beyond the CLI invocation:

    * The agent runs from ``ib-agent:codex``, built from its own Dockerfile, so
      a change here can never trigger a rebuild of the Claude lane's image.
    * ``total_cost_usd`` is always ``None`` — Codex reports token counts but no
      dollar figure. Do not backfill it from an API rate card: on a ChatGPT plan
      the marginal cost is zero and a computed number would be fiction.

    Results carry ``harness: codex`` and must never be pooled with the
    claude-code or bash lanes — different agent, own tool loop and prompt.
    """
    rollout_started = time.time()
    rollout_started_mono = time.monotonic()
    load_dotenv_files()
    task_dir = Path(task_dir)
    task = load_task_config(task_dir)

    ed = EvalDir.create(
        output_root=Path(output_root or DEFAULT_OUTPUT_ROOT),
        eval_id=eval_id,
        logical_rollout_id=logical_rollout_id,
    )
    model_label = model or "codex-default"
    auth_mode = codex_auth_mode()
    provenance = capture_provenance(task_dir, model=model_label, provider="codex", mode="codex")
    ed.write_meta(
        {
            "status": "running",
            "task": task.id,
            "model": model_label,
            "provider": "codex",
            "harness": "codex",
            "mode": "codex",
            "auth_mode": auth_mode,
            "provenance": provenance,
        }
    )
    repo_dir = stage_workspace_into_eval(task_dir, ed)
    workspace = ed.workspace

    stack = ComposeUnitStack(
        task_dir,
        ed,
        app_repo=repo_dir,
        include_agent=True,
        agent_codex=True,
    )
    provenance["agent_image"] = capture_image_identity(stack._agent_image)
    ed.write_meta({"provenance": provenance})
    # up() owns Compose rendering and one-shot agent-home provisioning.
    stack.up(exclude_app=True)

    problem = ""
    problem_path = workspace / "PROBLEM.md"
    if problem_path.is_file():
        problem = problem_path.read_text(encoding="utf-8", errors="replace")
    # Deliberately the SAME prompt template as the Claude Code lane. The lanes
    # differ in agent; holding the instructions byte-identical keeps that the
    # only variable when the two are compared.
    prompt = _CC_PROMPT.format(task_id=task.id, problem=problem[:20000])
    ed.append_transcript({"type": "user", "content": prompt[:4000]})

    timeout_s = int(task.timeout_minutes * 60)
    timed_out = False
    resource_limited = False
    error: str | None = None
    returncode: int | None = None
    started = time.time()
    started_mono = time.monotonic()

    cx_cmd = [
        "timeout",
        # Same escalation as the Claude lane: a CLI that traps SIGTERM would
        # otherwise outlive its budget. Measured there at 12,450s against a
        # 3,600s timeout, so this is not hypothetical.
        "--kill-after=60",
        str(timeout_s),
        "codex",
        "exec",
        prompt,
        "--json",
        # The container IS the sandbox. Codex's own seccomp/landlock layer adds
        # nothing here and blocks the vendor HTTP the task exists to exercise.
        "--dangerously-bypass-approvals-and-sandbox",
        # /workspace is staged, not cloned: no .git at its root.
        "--skip-git-repo-check",
        # CODEX_HOME is a shared host mount and rollouts run concurrently.
        # Persisted session files would have every rollout writing into one
        # directory; nothing needs resume here, so opt out of the writes.
        "--ephemeral",
        "-C",
        "/workspace",
    ]
    if model:
        cx_cmd += ["--model", model]
    effort = os.environ.get("IB_REASONING_EFFORT", "").strip()
    if effort and effort != "codex-config-pinned":
        cx_cmd += ["--config", f'model_reasoning_effort="{effort}"']

    usage: dict[str, Any] = {}
    usage_limit = False
    limit_reset_epoch: int | None = None
    turn_failed: str | None = None

    def note_limit(text: str) -> None:
        nonlocal usage_limit, limit_reset_epoch
        hit, reset = detect_codex_usage_limit(text)
        if hit:
            usage_limit = True
            if reset is not None:
                limit_reset_epoch = reset

    def handle_line(line: str) -> None:
        nonlocal usage, turn_failed
        if not line.strip():
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # Non-JSON output is the CLI speaking for itself (auth errors, the
            # plan-exhaustion notice), so it is safe to scan.
            note_limit(line)
            ed.append_agent_log(line)
            if stream:
                print(line, flush=True)
            return
        ed.append_transcript(event)
        if event.get("type") == "turn.completed":
            usage = event.get("usage") or {}
        elif event.get("type") == "turn.failed":
            err = event.get("error")
            turn_failed = str(
                (err.get("message") if isinstance(err, dict) else err) or "turn failed"
            )
            # Scan ONLY the failure envelope, never item text. See the comment
            # above _CODEX_LIMIT_TEXT.
            note_limit(json.dumps(err) if isinstance(err, dict) else str(err))
        for text in _codex_event_lines(event):
            ed.append_agent_log(text)
            if stream:
                print(text, flush=True)

    header = (
        f"$ codex exec <prompt {len(prompt)} chars> "
        "--json --dangerously-bypass-approvals-and-sandbox" + (f" --model {model}" if model else "")
    )
    ed.append_agent_log(header)
    if stream:
        print(header, flush=True)

    try:
        try:
            returncode = stack.agent_exec_stream(
                *cx_cmd,
                on_line=handle_line,
                timeout_s=timeout_s + 120,
                # `codex exec` consumes a piped stdin as an extra prompt block
                # and blocks waiting on it; the prompt is passed by argument.
                stdin_devnull=True,
            )
            ed.append_agent_log(f"exit={returncode}")
            if returncode == 124:
                timed_out = True
                error = "codex exceeded task timeout"
            elif returncode != 0:
                error = turn_failed or f"codex exited with status {returncode}"
        except subprocess.TimeoutExpired:
            timed_out = True
            error = "codex exec exceeded host timeout"
            ed.append_agent_log(error + "\n")
        except ParticipantResourceError as exc:
            resource_limited = True
            error = str(exc)
            ed.append_agent_log(error + "\n")
    finally:
        try:
            ed.write_vendor_log(stack.logs("vendors"))
        except Exception:
            pass
        if not keep:
            try:
                stack.down()
            except Exception:
                pass
            _cleanup_ephemeral_agent_home(ed, stack)
        else:
            _return_workspace_to_host(ed, stack)

    agent_finished = time.time()
    agent_finished_mono = time.monotonic()

    patch_text = _extract_candidate_diff(repo_dir, ed)
    ed.write_patch(patch_text)

    # Identical refusal to the Claude lane, for the identical reason: the diff
    # on disk after a cut-off run is partial, so grading it yields a low reward
    # indistinguishable from a genuine model failure, and an unattended sweep
    # would bank it silently. Refuse to grade; the driver re-runs it.
    if usage_limit:
        msg = "codex hit the plan usage limit"
        if limit_reset_epoch:
            msg += f" (resets at epoch {limit_reset_epoch})"
        ed.append_agent_log(msg)
        ed.write_meta(
            {
                "status": "usage_limit",
                "timed_out": timed_out,
                "resolved": False,
                "reward": None,
                "reward_semantics": "task_score_div_100",
                "scorer_version": SCORER_VERSION,
                "elapsed_s": time.time() - started,
                "timing": {
                    "setup_s": started_mono - rollout_started_mono,
                    "model_s": agent_finished_mono - started_mono,
                    "grading_s": None,
                    "total_s": agent_finished_mono - rollout_started_mono,
                    "wall_total_s": agent_finished - rollout_started,
                    "machine_sleep_s": max(
                        0.0,
                        (agent_finished - rollout_started)
                        - (agent_finished_mono - rollout_started_mono),
                    ),
                    "provider_queue_s": None,
                    "model_s_includes_provider_queue": True,
                },
                "agent_returncode": returncode,
                "usage": usage,
                "total_cost_usd": None,
                "auth_mode": auth_mode,
                "error": msg,
                "stop_reason": "usage_limit",
                "truncated": True,
                "graded": False,
                "limit_reset_epoch": limit_reset_epoch,
                "timeout_minutes": task.timeout_minutes,
                "failure_class": "provider_infrastructure_failure",
            }
        )
        return EvalResult(
            eval_id=ed.eval_id,
            task_id=task.id,
            model=model_label,
            provider="codex",
            turns=1,
            timed_out=timed_out,
            output_dir=ed.root,
            patch_path=ed.patch_path,
            verdict_path=None,
            resolved=False,
            reward=None,
            error=msg,
            stop_reason="usage_limit",
            failure_class="provider_infrastructure_failure",
        )

    grading_started_mono = time.monotonic()
    grade = grade_once(
        task_dir,
        ed.patch_path,
        run_id=ed.eval_id,
        keep=keep,
        eval_dir=ed,
    )
    grading_finished = time.time()
    grading_finished_mono = time.monotonic()
    verdict = grade.verdict
    stop_reason = "resource_limit" if resource_limited else "wall_clock" if timed_out else "done"
    failure_class = rollout_failure_class(stop_reason, grade.failure_class)
    if returncode not in (None, 0) and not timed_out:
        failure_class = "candidate_runtime_failure"
    reward = None if grade.task_score is None else grade.task_score / 100.0
    result_resolved, reward, result_error, failure_class = _security_outcome(
        ed,
        resolved=grade.solved,
        reward=reward,
        error=error or verdict.error,
        failure_class=failure_class,
    )

    ed.write_meta(
        {
            "status": "done",
            "timed_out": timed_out,
            "resolved": result_resolved,
            "legacy_resolved": verdict.resolved,
            "reward": reward,
            "reward_semantics": "task_score_div_100",
            "scorer_version": SCORER_VERSION,
            "raw_score": grade.raw_score,
            "task_score": grade.task_score,
            "check_coverage": grade.check_coverage,
            "missing_checks": grade.missing_checks,
            "failure_class": failure_class,
            "elapsed_s": time.time() - started,
            "timing": {
                "setup_s": started_mono - rollout_started_mono,
                "model_s": agent_finished_mono - started_mono,
                "grading_s": grading_finished_mono - grading_started_mono,
                "total_s": grading_finished_mono - rollout_started_mono,
                "wall_total_s": grading_finished - rollout_started,
                "machine_sleep_s": max(
                    0.0,
                    (grading_finished - rollout_started)
                    - (grading_finished_mono - rollout_started_mono),
                ),
                "provider_queue_s": None,
                "model_s_includes_provider_queue": True,
            },
            "agent_returncode": returncode,
            "usage": usage,
            "total_cost_usd": None,
            "auth_mode": auth_mode,
            "error": result_error,
            "stop_reason": stop_reason,
            "truncated": bool(timed_out),
            "timeout_minutes": task.timeout_minutes,
        }
    )

    return EvalResult(
        eval_id=ed.eval_id,
        task_id=task.id,
        model=model_label,
        provider="codex",
        turns=1,
        timed_out=timed_out,
        output_dir=ed.root,
        patch_path=ed.patch_path,
        verdict_path=ed.verdict_path if ed.verdict_path.is_file() else None,
        resolved=result_resolved,
        reward=reward,
        error=result_error,
        stop_reason=stop_reason,
        raw_score=grade.raw_score,
        task_score=grade.task_score,
        check_coverage=grade.check_coverage,
        missing_checks=grade.missing_checks,
        failure_class=failure_class,
    )


def _opencode_usage_and_cost(event: dict[str, Any]) -> tuple[dict[str, Any] | None, float | None]:
    """Extract OpenCode's step-finish accounting without depending on UI text."""
    part = event.get("part") if isinstance(event.get("part"), dict) else event
    tokens = part.get("tokens") if isinstance(part, dict) else None
    cost = part.get("cost") if isinstance(part, dict) else None
    if isinstance(tokens, dict):
        cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
        usage = {
            "input_tokens": tokens.get("input", 0),
            "cached_input_tokens": cache.get("read", 0),
            "cache_write_input_tokens": cache.get("write", 0),
            "output_tokens": tokens.get("output", 0),
            "reasoning_output_tokens": tokens.get("reasoning", 0),
        }
        return usage, float(cost) if isinstance(cost, (int, float)) else None
    return None, None


def eval_opencode_once(
    task_dir: Path,
    *,
    model: str,
    variant: str | None = None,
    keep: bool = False,
    output_root: Path | None = None,
    eval_id: str | None = None,
    logical_rollout_id: str | None = None,
    stream: bool = True,
) -> EvalResult:
    """Run the real OpenCode CLI against an OpenRouter model, then grade."""
    rollout_started = time.time()
    rollout_started_mono = time.monotonic()
    load_dotenv_files()
    require_api_key(resolve_provider("openrouter/dummy")[0])
    task_dir = Path(task_dir)
    task = load_task_config(task_dir)
    concrete = model.removeprefix("openrouter/")
    cli_model = f"openrouter/{concrete}"

    ed = EvalDir.create(
        output_root=Path(output_root or DEFAULT_OUTPUT_ROOT),
        eval_id=eval_id,
        logical_rollout_id=logical_rollout_id,
    )
    provenance = capture_provenance(
        task_dir,
        model=concrete,
        provider="opencode",
        mode="opencode",
        requested_effort=variant or "unspecified",
    )
    ed.write_meta(
        {
            "status": "running",
            "task": task.id,
            "model": concrete,
            "provider": "openrouter",
            "harness": "opencode",
            "mode": "opencode",
            "auth_mode": "api_key",
            "opencode_variant": variant,
            "provenance": provenance,
        }
    )
    repo_dir = stage_workspace_into_eval(task_dir, ed)
    stack = ComposeUnitStack(
        task_dir,
        ed,
        app_repo=repo_dir,
        include_agent=True,
        agent_opencode=True,
        opencode_model=concrete,
    )
    provenance["agent_image"] = capture_image_identity(stack._agent_image)
    ed.write_meta({"provenance": provenance})
    # up() owns Compose rendering and one-shot agent-home provisioning.
    stack.up(exclude_app=True)

    problem_path = ed.workspace / "PROBLEM.md"
    problem = (
        problem_path.read_text(encoding="utf-8", errors="replace") if problem_path.is_file() else ""
    )
    prompt = _CC_PROMPT.format(task_id=task.id, problem=problem[:20000])
    ed.append_transcript({"type": "user", "content": prompt[:4000]})
    timeout_s = int(task.timeout_minutes * 60)
    started = time.time()
    started_mono = time.monotonic()
    timed_out = False
    resource_limited = False
    error: str | None = None
    returncode: int | None = None
    usage: dict[str, Any] = {}
    cost_usd = 0.0
    cost_seen = False
    provider_failure = False
    usage_limit = False

    cmd = [
        "timeout",
        "--kill-after=60",
        str(timeout_s),
        "opencode",
        "run",
        prompt,
        "--format",
        "json",
        "--model",
        cli_model,
        "--dir",
        "/workspace",
        "--auto",
        "--pure",
        "--thinking",
    ]
    if variant:
        cmd += ["--variant", variant]

    def handle_line(line: str) -> None:
        nonlocal usage, cost_usd, cost_seen, provider_failure, usage_limit
        if not line.strip():
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            ed.append_agent_log(line)
            low = line.lower()
            if "429" in low or "rate limit" in low or "insufficient" in low:
                usage_limit = True
            if "error" in low:
                provider_failure = True
            if stream:
                print(line, flush=True)
            return
        ed.append_transcript(event)
        u, c = _opencode_usage_and_cost(event)
        if u is not None:
            for key, value in u.items():
                usage[key] = usage.get(key, 0) + int(value or 0)
            if c is not None:
                cost_usd += c
                cost_seen = True
        encoded = json.dumps(event, ensure_ascii=False)
        etype = str(event.get("type", ""))
        if "error" in etype.lower():
            provider_failure = True
            low = encoded.lower()
            if "429" in low or "rate limit" in low or "insufficient" in low:
                usage_limit = True
        ed.append_agent_log(encoded[:4000] + "\n")
        if stream:
            print(encoded[:1000], flush=True)

    ed.append_agent_log(
        f"$ opencode run <prompt {len(prompt)} chars> --format json --model {cli_model}"
        + (f" --variant {variant}" if variant else "")
        + "\n"
    )
    try:
        try:
            returncode = stack.agent_exec_stream(
                *cmd, on_line=handle_line, timeout_s=timeout_s + 120, stdin_devnull=True
            )
            ed.append_agent_log(f"exit={returncode}\n")
            if returncode == 124:
                timed_out = True
                error = "opencode exceeded task timeout"
            elif returncode != 0:
                error = f"opencode exited with status {returncode}"
        except subprocess.TimeoutExpired:
            timed_out = True
            error = "opencode exec exceeded host timeout"
        except ParticipantResourceError as exc:
            resource_limited = True
            error = str(exc)
            ed.append_agent_log(error + "\n")
    finally:
        try:
            ed.write_vendor_log(stack.logs("vendors"))
        except Exception:
            pass
        if not keep:
            try:
                stack.down()
            except Exception:
                pass
            _cleanup_ephemeral_agent_home(ed, stack)
        else:
            _return_workspace_to_host(ed, stack)

    agent_finished = time.time()
    agent_finished_mono = time.monotonic()
    ed.write_patch(_extract_candidate_diff(repo_dir, ed))
    if usage_limit or provider_failure:
        reason = "usage_limit" if usage_limit else "provider_error"
        msg = "OpenRouter/OpenCode provider failure; rollout not graded"
        ed.write_meta(
            {
                "status": reason,
                "resolved": False,
                "reward": None,
                "scorer_version": SCORER_VERSION,
                "elapsed_s": agent_finished - started,
                "agent_returncode": returncode,
                "usage": usage,
                "total_cost_usd": cost_usd if cost_seen else None,
                "error": msg,
                "stop_reason": reason,
                "truncated": True,
                "graded": False,
                "failure_class": "provider_infrastructure_failure",
            }
        )
        return EvalResult(
            ed.eval_id,
            task.id,
            concrete,
            "openrouter",
            1,
            timed_out,
            ed.root,
            ed.patch_path,
            None,
            False,
            None,
            msg,
            stop_reason=reason,
            failure_class="provider_infrastructure_failure",
        )

    grading_started_mono = time.monotonic()
    grade = grade_once(task_dir, ed.patch_path, run_id=ed.eval_id, keep=keep, eval_dir=ed)
    grading_finished = time.time()
    grading_finished_mono = time.monotonic()
    stop_reason = "resource_limit" if resource_limited else "wall_clock" if timed_out else "done"
    failure_class = rollout_failure_class(stop_reason, grade.failure_class)
    if returncode not in (None, 0) and not timed_out:
        failure_class = "candidate_runtime_failure"
    reward = None if grade.task_score is None else grade.task_score / 100.0
    result_resolved, reward, result_error, failure_class = _security_outcome(
        ed,
        resolved=grade.solved,
        reward=reward,
        error=error or grade.verdict.error,
        failure_class=failure_class,
    )
    ed.write_meta(
        {
            "status": "done",
            "timed_out": timed_out,
            "resolved": result_resolved,
            "legacy_resolved": grade.verdict.resolved,
            "reward": reward,
            "reward_semantics": "task_score_div_100",
            "scorer_version": SCORER_VERSION,
            "raw_score": grade.raw_score,
            "task_score": grade.task_score,
            "check_coverage": grade.check_coverage,
            "missing_checks": grade.missing_checks,
            "failure_class": failure_class,
            "elapsed_s": grading_finished - started,
            "timing": {
                "setup_s": started_mono - rollout_started_mono,
                "model_s": agent_finished_mono - started_mono,
                "grading_s": grading_finished_mono - grading_started_mono,
                "total_s": grading_finished_mono - rollout_started_mono,
                "wall_total_s": grading_finished - rollout_started,
                "provider_queue_s": None,
                "model_s_includes_provider_queue": True,
            },
            "agent_returncode": returncode,
            "usage": usage,
            "total_cost_usd": cost_usd if cost_seen else None,
            "error": result_error,
            "stop_reason": stop_reason,
            "truncated": timed_out,
            "timeout_minutes": task.timeout_minutes,
        }
    )
    return EvalResult(
        ed.eval_id,
        task.id,
        concrete,
        "openrouter",
        1,
        timed_out,
        ed.root,
        ed.patch_path,
        ed.verdict_path if ed.verdict_path.is_file() else None,
        result_resolved,
        reward,
        result_error,
        stop_reason=stop_reason,
        raw_score=grade.raw_score,
        task_score=grade.task_score,
        check_coverage=grade.check_coverage,
        missing_checks=grade.missing_checks,
        failure_class=failure_class,
    )
