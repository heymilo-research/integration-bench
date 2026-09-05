"""Direct LLM providers for public ``bench eval``.

Supported env keys (from repo ``.env``):

- ``ANTHROPIC_API_KEY``
- ``OPENAI_API_KEY``
- ``DEEPSEEK_API_KEY``
- ``OPENROUTER_API_KEY``
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx


class ProviderError(RuntimeError):
    """Missing key or HTTP failure talking to a provider."""


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    api_key_env: str
    base_url: str
    kind: str  # "anthropic" | "openai_compatible"
    default_model: str


PROVIDERS: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(
        name="anthropic",
        api_key_env="ANTHROPIC_API_KEY",
        base_url="https://api.anthropic.com",
        kind="anthropic",
        default_model="claude-sonnet-4-20250514",
    ),
    "openai": ProviderSpec(
        name="openai",
        api_key_env="OPENAI_API_KEY",
        base_url="https://api.openai.com/v1",
        kind="openai_compatible",
        default_model="gpt-4.1",
    ),
    "deepseek": ProviderSpec(
        name="deepseek",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com",
        kind="openai_compatible",
        default_model="deepseek-chat",
    ),
    "openrouter": ProviderSpec(
        name="openrouter",
        api_key_env="OPENROUTER_API_KEY",
        base_url="https://openrouter.ai/api/v1",
        kind="openai_compatible",
        default_model="anthropic/claude-sonnet-4",
    ),
}


def load_dotenv_files(start: Path | None = None) -> None:
    """Load provider keys from ``.env`` or the private ``.secrets/.env``.

    The private path keeps keys out of a public benchmark checkout while still
    making `bench eval*` clone-and-run friendly. Environment variables always
    win (`override=False`).
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    cur = (start or Path.cwd()).resolve()
    for p in [cur, *cur.parents[:8]]:
        for env in (p / ".secrets" / ".env", p / ".env"):
            if env.is_file():
                load_dotenv(env, override=False)
                return


def resolve_provider(model: str) -> tuple[ProviderSpec, str]:
    """Map a model string to (provider, concrete model id)."""
    raw = (model or "").strip()
    lower = raw.lower()

    if lower.startswith("openrouter/"):
        return PROVIDERS["openrouter"], raw.split("/", 1)[1]
    if lower.startswith("anthropic/"):
        return PROVIDERS["anthropic"], raw.split("/", 1)[1]
    if lower.startswith("openai/"):
        return PROVIDERS["openai"], raw.split("/", 1)[1]
    if lower.startswith("deepseek/"):
        return PROVIDERS["deepseek"], raw.split("/", 1)[1]

    if lower in ("sonnet", "haiku", "opus", "claude", "claude-sonnet", "claude-haiku"):
        alias = {
            "sonnet": "claude-sonnet-4-20250514",
            "claude": "claude-sonnet-4-20250514",
            "claude-sonnet": "claude-sonnet-4-20250514",
            "haiku": "claude-haiku-4-5-20251001",
            "claude-haiku": "claude-haiku-4-5-20251001",
            "opus": "claude-opus-4-20250514",
        }[lower]
        return PROVIDERS["anthropic"], alias
    if lower.startswith("claude"):
        return PROVIDERS["anthropic"], raw
    if lower.startswith("gpt-") or lower.startswith("o1") or lower.startswith("o3"):
        return PROVIDERS["openai"], raw
    if "deepseek" in lower:
        return PROVIDERS["deepseek"], raw
    if "/" in raw:
        return PROVIDERS["openrouter"], raw

    raise ProviderError(
        f"cannot route model {model!r} to a provider. "
        "Use sonnet|haiku|gpt-…|deepseek-…|openrouter/<vendor>/<model> "
        "or prefix anthropic/|openai/|deepseek/|openrouter/."
    )


def require_api_key(spec: ProviderSpec) -> str:
    key = (os.environ.get(spec.api_key_env) or "").strip()
    if not key:
        raise ProviderError(
            f"missing {spec.api_key_env} — set it in .env "
            f"(see .env.example) before running bench eval with this model"
        )
    return key


TOOL_SCHEMA_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command in the task workspace (cwd is the workspace root).",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file relative to the workspace root.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write UTF-8 text to a path relative to the workspace root (creates parents).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "Signal that the integration work is complete and ready to grade.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

TOOL_SCHEMA_ANTHROPIC = [
    {
        "name": t["function"]["name"],
        "description": t["function"]["description"],
        "input_schema": t["function"]["parameters"],
    }
    for t in TOOL_SCHEMA_OPENAI
]

# /v1/responses wants flat function tools, not chat/completions' nested shape.
TOOL_SCHEMA_RESPONSES = [
    {
        "type": "function",
        "name": t["function"]["name"],
        "description": t["function"]["description"],
        "parameters": t["function"]["parameters"],
    }
    for t in TOOL_SCHEMA_OPENAI
]

# These models reject function tools on /v1/chat/completions unless
# reasoning_effort='none'. Disabling reasoning would understate them on an
# agentic bench, so they go through /v1/responses, which supports tools AND
# reasoning. Extend as newer reasoning-only models land.
_RESPONSES_MODEL_PREFIXES = ("gpt-5.6",)


def _needs_responses_api(model: str) -> bool:
    return model.strip().lower().startswith(_RESPONSES_MODEL_PREFIXES)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class AssistantTurn:
    content: str
    tool_calls: list[ToolCall]
    raw: dict[str, Any]
    usage: dict[str, int] = field(default_factory=dict)


def _norm_usage(raw: Any) -> dict[str, int]:
    """Normalise provider usage to a common shape.

    Anthropic reports ``input_tokens`` as the *uncached* remainder, with
    cache-write/read counts alongside; they are kept separate because they price
    differently (~1.25x write, ~0.1x read). OpenAI-compatible providers use
    ``prompt_tokens``/``completion_tokens`` and report no cache split.
    """
    if not isinstance(raw, dict):
        return {}
    out = {
        "input_tokens": raw.get("input_tokens") or raw.get("prompt_tokens") or 0,
        "output_tokens": raw.get("output_tokens") or raw.get("completion_tokens") or 0,
        "cache_creation_input_tokens": raw.get("cache_creation_input_tokens") or 0,
        "cache_read_input_tokens": raw.get("cache_read_input_tokens") or 0,
    }
    return {k: int(v) for k, v in out.items()}


# Long-context agentic turns with thinking on routinely exceed 120s; a whole
# multi-hour rollout used to die on one slow response (httpx.ReadTimeout is not
# a ProviderError, so it escaped eval_core's handler and discarded patch+grade).
_DEFAULT_TIMEOUT_S = 600.0
# A 3-attempt / ~25s window was too thin: a local DNS blip (ConnectError
# "nodename nor servname provided") ended task-0022 at turn 113 after 2.97 h.
# ~110s of retry rides out transient resolution/connection failures, and the
# cost of over-waiting is trivial next to losing a multi-hour rollout.
_TRANSIENT_ATTEMPTS = 5
_TRANSIENT_BACKOFF_S = (5.0, 15.0, 30.0, 60.0)


def chat(
    *,
    provider: ProviderSpec,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> AssistantTurn:
    """Dispatch one turn, retrying transient transport failures.

    Terminal failures are raised as ProviderError so the caller can end the
    rollout and still grade the work done so far, rather than crashing.
    """
    last: Exception | None = None
    for attempt in range(_TRANSIENT_ATTEMPTS):
        try:
            return _chat_once(
                provider=provider,
                model=model,
                system=system,
                messages=messages,
                timeout_s=timeout_s,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = exc
            if attempt < _TRANSIENT_ATTEMPTS - 1:
                time.sleep(_TRANSIENT_BACKOFF_S[min(attempt, len(_TRANSIENT_BACKOFF_S) - 1)])
    raise ProviderError(
        f"{provider.name} transport failure after {_TRANSIENT_ATTEMPTS} attempts: "
        f"{type(last).__name__}: {last}"
    )


def _chat_once(
    *,
    provider: ProviderSpec,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    timeout_s: float,
) -> AssistantTurn:
    key = require_api_key(provider)
    if provider.kind == "anthropic":
        return _chat_anthropic(
            base_url=provider.base_url,
            api_key=key,
            model=model,
            system=system,
            messages=messages,
            timeout_s=timeout_s,
        )
    # provider.name, not provider.kind: every OpenAI-compatible provider shares
    # kind="openai_compatible", and only api.openai.com serves /v1/responses.
    if provider.name == "openai" and _needs_responses_api(model):
        return _chat_openai_responses(
            base_url=provider.base_url,
            api_key=key,
            model=model,
            system=system,
            messages=messages,
            timeout_s=timeout_s,
        )
    return _chat_openai_compatible(
        base_url=provider.base_url,
        api_key=key,
        model=model,
        system=system,
        messages=messages,
        timeout_s=timeout_s,
    )


def _chat_openai_compatible(
    *,
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    timeout_s: float,
) -> AssistantTurn:
    body_messages = [{"role": "system", "content": system}, *messages]
    payload = {
        "model": model,
        "messages": body_messages,
        "tools": TOOL_SCHEMA_OPENAI,
        "tool_choice": "auto",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=timeout_s) as client:
        resp = client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json=payload,
        )
    if resp.status_code >= 400:
        raise ProviderError(f"provider HTTP {resp.status_code}: {resp.text[:800]}")
    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    tool_calls: list[ToolCall] = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        args_raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
        except json.JSONDecodeError:
            args = {"_raw": args_raw}
        tool_calls.append(
            ToolCall(
                id=str(tc.get("id") or f"call_{len(tool_calls)}"),
                name=str(fn.get("name") or ""),
                arguments=args,
            )
        )
    return AssistantTurn(
        content=content,
        tool_calls=tool_calls,
        raw=msg,
        usage=_norm_usage(data.get("usage")),
    )


def _openai_messages_to_anthropic(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(messages):
        m = messages[i]
        role = m.get("role")
        if role == "assistant":
            content_blocks: list[dict[str, Any]] = []
            if m.get("content"):
                content_blocks.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                args_raw = fn.get("arguments") or "{}"
                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
                except json.JSONDecodeError:
                    args = {}
                content_blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id"),
                        "name": fn.get("name"),
                        "input": args,
                    }
                )
            out.append({"role": "assistant", "content": content_blocks or ""})
            i += 1
        elif role == "tool":
            tool_results: list[dict[str, Any]] = []
            while i < len(messages) and messages[i].get("role") == "tool":
                tm = messages[i]
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tm.get("tool_call_id"),
                        "content": tm.get("content") or "",
                    }
                )
                i += 1
            out.append({"role": "user", "content": tool_results})
        elif role == "user":
            out.append({"role": "user", "content": m.get("content") or ""})
            i += 1
        else:
            i += 1
    return out


def _chat_anthropic(
    *,
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    timeout_s: float,
) -> AssistantTurn:
    payload = {
        "model": model,
        "max_tokens": 8192,
        "system": system,
        "tools": TOOL_SCHEMA_ANTHROPIC,
        "messages": _openai_messages_to_anthropic(messages),
        # Agent loops resend the whole transcript every turn, so the prefix is
        # append-only and grows monotonically -- the ideal caching shape.
        # Auto-placement puts the breakpoint on the last cacheable block, so each
        # turn reads the prior prefix (~0.1x) instead of re-paying full price.
        # Without this, input is ~90% of spend and none of it is cached.
        "cache_control": {"type": "ephemeral"},
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    with httpx.Client(timeout=timeout_s) as client:
        resp = client.post(
            f"{base_url.rstrip('/')}/v1/messages",
            headers=headers,
            json=payload,
        )
    if resp.status_code >= 400:
        raise ProviderError(f"anthropic HTTP {resp.status_code}: {resp.text[:800]}")
    data = resp.json()
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    openai_tool_calls: list[dict[str, Any]] = []
    for block in data.get("content") or []:
        if block.get("type") == "text":
            text_parts.append(block.get("text") or "")
        elif block.get("type") == "tool_use":
            tid = str(block.get("id") or f"tool_{len(tool_calls)}")
            name = str(block.get("name") or "")
            args = block.get("input") or {}
            tool_calls.append(ToolCall(id=tid, name=name, arguments=args))
            openai_tool_calls.append(
                {
                    "id": tid,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            )
    raw = {
        "role": "assistant",
        "content": "\n".join(text_parts),
        "tool_calls": openai_tool_calls,
    }
    return AssistantTurn(
        content="\n".join(text_parts),
        tool_calls=tool_calls,
        raw=raw,
        usage=_norm_usage(data.get("usage")),
    )


def _to_responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """openai chat messages -> /v1/responses input items."""
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            out.append(
                {
                    "type": "function_call_output",
                    "call_id": str(m.get("tool_call_id") or ""),
                    "output": str(m.get("content") or ""),
                }
            )
        elif role == "assistant":
            if m.get("content"):
                out.append({"role": "assistant", "content": str(m["content"])})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                args = fn.get("arguments")
                if not isinstance(args, str):
                    args = json.dumps(args or {})
                out.append(
                    {
                        "type": "function_call",
                        "call_id": str(tc.get("id") or ""),
                        "name": str(fn.get("name") or ""),
                        "arguments": args,
                    }
                )
        else:
            out.append({"role": role or "user", "content": str(m.get("content") or "")})
    return out


def _norm_responses_usage(raw: Any) -> dict[str, int]:
    """/v1/responses usage -> the common shape.

    ``input_tokens`` here already includes cached tokens, so the cached count is
    subtracted out and reported separately -- otherwise it would be billed twice.
    """
    if not isinstance(raw, dict):
        return {}
    det_in = raw.get("input_tokens_details") or {}
    det_out = raw.get("output_tokens_details") or {}
    cached = int(det_in.get("cached_tokens") or 0)
    return {
        "input_tokens": max(0, int(raw.get("input_tokens") or 0) - cached),
        "output_tokens": int(raw.get("output_tokens") or 0),
        "cache_read_input_tokens": cached,
        "cache_creation_input_tokens": int(det_in.get("cache_write_tokens") or 0),
        "reasoning_tokens": int(det_out.get("reasoning_tokens") or 0),
    }


def _chat_openai_responses(
    *,
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    timeout_s: float,
) -> AssistantTurn:
    payload = {
        "model": model,
        "instructions": system,
        "input": _to_responses_input(messages),
        "tools": TOOL_SCHEMA_RESPONSES,
    }
    headers = {"Authorization": f"Bearer {api_key}", "content-type": "application/json"}
    with httpx.Client(timeout=timeout_s) as client:
        resp = client.post(f"{base_url.rstrip('/')}/responses", headers=headers, json=payload)
    if resp.status_code >= 400:
        raise ProviderError(f"responses HTTP {resp.status_code}: {resp.text[:800]}")
    data = resp.json()

    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    openai_tool_calls: list[dict[str, Any]] = []
    for item in data.get("output") or []:
        itype = item.get("type")
        if itype == "message":
            for c in item.get("content") or []:
                if c.get("type") in ("output_text", "text"):
                    text_parts.append(c.get("text") or "")
        elif itype == "function_call":
            cid = str(item.get("call_id") or f"call_{len(tool_calls)}")
            name = str(item.get("name") or "")
            args_raw = item.get("arguments") or "{}"
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
            except json.JSONDecodeError:
                args = {"_raw": args_raw}
            tool_calls.append(ToolCall(id=cid, name=name, arguments=args))
            openai_tool_calls.append(
                {
                    "id": cid,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            )

    content = "\n".join(p for p in text_parts if p)
    raw = {"role": "assistant", "content": content, "tool_calls": openai_tool_calls}
    return AssistantTurn(
        content=content,
        tool_calls=tool_calls,
        raw=raw,
        usage=_norm_responses_usage(data.get("usage")),
    )


_MODEL_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def safe_run_id_fragment(model: str) -> str:
    return _MODEL_SAFE.sub("-", model)[:48] or "model"
