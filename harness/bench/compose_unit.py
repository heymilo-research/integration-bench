"""Per-eval Docker Compose unit: standalone vendors + agent + app.

One compose project is created per ``eval_id``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse, urlunparse

import yaml

from bench.canonical_sqlite import CANONICAL_DATABASE_URL, CANONICAL_DB_PATH, reset_db_file
from bench.compose import ComposeError, ParticipantDiskLimitExceeded
from bench.config import TaskConfig, load_task_config
from bench.eval_output import EvalDir
from bench.health import wait_for_http
from bench.images import load_images_lock, resolve_vendor_image

AGENT_IMAGE = os.environ.get("IB_AGENT_IMAGE", "ib-agent:local")
CODEX_AGENT_IMAGE = os.environ.get("IB_CODEX_AGENT_IMAGE", "ib-agent:codex")
OPENCODE_AGENT_IMAGE = os.environ.get("IB_OPENCODE_AGENT_IMAGE", "ib-agent:opencode")

# Claude Code subscription-harness container home. Config is bind-mounted
# read-write because the Linux CLI refreshes OAuth tokens in its credentials
# file. On macOS the source token lives in Keychain and is seeded below.
_CLAUDE_CONFIG_IN_CONTAINER = "/claude-work"
_CLAUDE_AGENT_HOME = "/home/agent"
_CLAUDE_ROLLOUT_STATE = {
    # Suppress first-run UI without importing the operator's machine identity,
    # project registry, feature caches, usage cache, or session history.
    "hasCompletedOnboarding": True,
    "autoUpdates": False,
}
_CLAUDE_ROLLOUT_SETTINGS = {
    # The CLI is also invoked with --dangerously-skip-permissions. Keeping the
    # acknowledgement here avoids an interactive first-use prompt while model
    # and effort remain explicit command-line/provenance inputs.
    "skipDangerousModePermissionPrompt": True,
}

# Codex CLI subscription-harness container home. `CODEX_HOME` is the single
# knob that relocates both config.toml and auth.json, and OpenAI documents
# auth.json as portable between hosts — so unlike the Claude lane there is no
# Keychain seeding step on macOS: the host file is already the file the Linux
# CLI wants. Mounted read-write because the CLI refreshes its OAuth token in
# place and must be able to persist the new one.
_CODEX_HOME_IN_CONTAINER = "/codex-home"
_OPENCODE_HOME_IN_CONTAINER = "/opencode-home"
_EGRESS_PROXY_IN_CONTAINER = "/opt/ib/egress_proxy.py"
_EGRESS_PROXY_URL = "http://egress-proxy:3128"
_PROVIDER_GATEWAY_IN_CONTAINER = "/opt/ib/provider_gateway.py"
_PROVIDER_GATEWAY_SERVICE = "openrouter-gateway"
_PROVIDER_GATEWAY_PORT = 8080
_PROVIDER_GATEWAY_URL = f"http://{_PROVIDER_GATEWAY_SERVICE}:{_PROVIDER_GATEWAY_PORT}/api/v1"
_WORKLOAD_NETWORK = "workload-net"
_VERIFIER_NETWORK = "verifier-net"
_EGRESS_NETWORK = "egress-net"
_APP_GATEWAY = "app-gateway"
_APP_GATEWAY_PORT = 4000
_CONTAINER_PIDS_LIMIT = 512
_TRUSTED_SERVICE_CPUS = 1.0
_TRUSTED_SERVICE_MEMORY = "512m"

# Exact provider hosts only. The CONNECT proxy rejects suffix/wildcard matches,
# IP literals and every port except 443. ChatGPT-plan Codex auth uses the
# ChatGPT backend and may refresh through auth.openai.com; API-key Codex uses
# api.openai.com. Keeping both here supports the two validated auth.json modes
# without opening unrelated OpenAI/ChatGPT properties.
_CLAUDE_EGRESS_HOSTS = ("api.anthropic.com", "claude.ai")
_CODEX_EGRESS_HOSTS = ("api.openai.com", "auth.openai.com", "chatgpt.com")
_OPENCODE_EGRESS_HOSTS = ("openrouter.ai",)
_SECRET_FIELD_RE = re.compile(r"(?:api[_-]?key|credential|password|refresh|secret|token)", re.I)


def _agent_egress_hosts(*, claude_code: bool, codex: bool, opencode: bool) -> tuple[str, ...]:
    """Provider allowlist for a containerized agent lane.

    The direct-provider loop calls its model API from the host, so its generic
    tools container deliberately receives an empty allowlist.
    """
    if claude_code:
        return _CLAUDE_EGRESS_HOSTS
    if codex:
        return _CODEX_EGRESS_HOSTS
    if opencode:
        return _OPENCODE_EGRESS_HOSTS
    return ()


def _credential_values(value: Any, *, key: str = "") -> list[str]:
    """Extract credential leaves without persisting or logging their values."""
    if isinstance(value, dict):
        out: list[str] = []
        for child_key, child in value.items():
            out.extend(_credential_values(child, key=str(child_key)))
        return out
    if isinstance(value, list):
        out = []
        for child in value:
            out.extend(_credential_values(child, key=key))
        return out
    if isinstance(value, str) and _SECRET_FIELD_RE.search(key):
        return [value]
    return []


def _proxy_environment(vendor_services: list[str] | None = None) -> dict[str, str]:
    """Proxy configuration understood by curl, Node/Bun CLIs and Codex."""
    local_hosts = [
        *(vendor_services or []),
        "app",
        "connector",
        "app-gateway",
        "egress-proxy",
        _PROVIDER_GATEWAY_SERVICE,
        "localhost",
        "127.0.0.1",
    ]
    no_proxy = ",".join(dict.fromkeys(local_hosts))
    return {
        "HTTP_PROXY": _EGRESS_PROXY_URL,
        "HTTPS_PROXY": _EGRESS_PROXY_URL,
        "http_proxy": _EGRESS_PROXY_URL,
        "https_proxy": _EGRESS_PROXY_URL,
        "NO_PROXY": no_proxy,
        "no_proxy": no_proxy,
        # Prevent Claude Code's optional marketplace/telemetry/update traffic
        # from adding irrelevant denied requests to every rollout.
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }


def _valid_claude_oauth_file(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        oauth = data["claudeAiOauth"]
        return bool(oauth.get("accessToken") and oauth.get("refreshToken"))
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return False


# Re-seed a little before the token actually dies, so a long rollout does not
# expire mid-run.
_CLAUDE_OAUTH_SKEW_MS = 5 * 60 * 1000


def _claude_oauth_expired(path: Path) -> bool:
    """True when the file's OAuth token is at/near expiry.

    Deliberately separate from :func:`_valid_claude_oauth_file`, which is a
    *shape* check. On Linux the CLI refreshes its own token in place using
    ``refreshToken``, so expiry must not invalidate the file there; this is
    consulted only on the macOS re-seed path.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        expires_at = data["claudeAiOauth"].get("expiresAt")
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return True
    if not isinstance(expires_at, (int, float)):
        return True
    return expires_at - _CLAUDE_OAUTH_SKEW_MS <= time.time() * 1000


def _seed_claude_credentials_from_macos_keychain(work_dir: Path, credentials_path: Path) -> None:
    """Seed Linux-compatible credentials from the macOS Claude Code Keychain.

    Native Claude Code stores each ``CLAUDE_CONFIG_DIR`` profile under
    ``Claude Code-credentials-<sha256(path)[:8]>``. The Keychain value is
    already the JSON envelope consumed by the Linux CLI. Never print it.
    """
    # NOTE: the freshness check is load-bearing. Checking only shape here meant
    # that once a stale file existed it was never replaced, so every subsequent
    # run mounted a dead token and the agent reported "Not logged in" while the
    # host Keychain held a perfectly good one. That failure is silent: the
    # rollout still grades, scoring 0.0, so a whole sweep reads as a terrible
    # model rather than a broken lane.
    if sys.platform != "darwin":
        return
    if _valid_claude_oauth_file(credentials_path) and not _claude_oauth_expired(credentials_path):
        return
    service_hash = hashlib.sha256(str(work_dir.resolve()).encode()).hexdigest()[:8]
    service = f"Claude Code-credentials-{service_hash}"
    result = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-w"],
        capture_output=True,
        text=True,
        errors="replace",
    )
    if result.returncode != 0:
        raise ComposeError(
            f"Claude Code credentials missing from macOS Keychain ({service}); "
            f"run CLAUDE_CONFIG_DIR={work_dir} claude once to authenticate"
        )
    try:
        data = json.loads(result.stdout)
        oauth = data["claudeAiOauth"]
        if not oauth.get("accessToken") or not oauth.get("refreshToken"):
            raise KeyError("OAuth tokens")
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ComposeError(
            f"macOS Keychain item {service} is not valid Claude Code OAuth data"
        ) from exc
    # Refuse to copy a dead token into the container. Without this the lane
    # mounts an expired credential, every rollout reports "Not logged in",
    # grades 0.0, and a 50-task sweep reads as a catastrophically bad model
    # instead of an auth failure. Fail loud, with the remedy.
    expires_at = oauth.get("expiresAt")
    if (
        not isinstance(expires_at, (int, float))
        or expires_at - _CLAUDE_OAUTH_SKEW_MS <= time.time() * 1000
    ):
        raise ComposeError(
            f"macOS Keychain item {service} holds an EXPIRED Claude Code token "
            f"(expiresAt={expires_at!r}). Refresh it on the host with:\n"
            f"  CLAUDE_CONFIG_DIR={work_dir} claude -p hi\n"
            "then re-run. (The agent would otherwise score 0.0 on every task.)"
        )
    credentials_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(credentials_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
        fh.write("\n")
    credentials_path.chmod(0o600)


def _make_claude_path_container_owned(path: Path, agent_image: str) -> None:
    """On native Linux, assign a rollout-local path to the image's agent.

    Docker Desktop translates bind-mount ownership through its macOS VM.  A
    native Linux Docker host does not: a mode-0600 credential copied by an
    arbitrary VM operator remains owned by that operator, while the image's
    ``agent`` user has a different numeric uid and Claude reports "Not logged
    in".  Use the already-required Docker daemon as the privileged boundary to
    assign the rollout-local path to the uid/gid declared by the pinned agent
    image.  This is used for both the private Claude home and the disposable
    workspace: the former must remain mode 0600, and the latter must be
    writable for Edit/Write tools.  Neither operation touches the source task
    checkout or the operator's source credential.
    """
    if sys.platform != "linux":
        return
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            "0:0",
            "--volume",
            f"{path.resolve()}:/target",
            "--entrypoint",
            "sh",
            agent_image,
            "-c",
            'chown -R "$(id -u agent):$(id -g agent)" /target',
        ],
        capture_output=True,
        text=True,
        errors="replace",
    )
    if result.returncode != 0:
        raise ComposeError(
            "could not provision a rollout path for the container "
            f"agent uid: {(result.stderr or result.stdout).strip()}"
        )


def make_path_host_owned(path: Path, agent_image: str) -> None:
    """Return a container-owned rollout path to the invoking host user.

    Native Linux bind mounts retain the numeric uid written by the agent image.
    Use the same Docker privilege boundary used during provisioning so cleanup
    can traverse and securely remove CLI session and credential files.
    """
    if sys.platform != "linux" or not path.exists():
        return
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            "0:0",
            "--volume",
            f"{path.resolve()}:/target",
            "--entrypoint",
            "sh",
            agent_image,
            "-c",
            f"chown -R {os.getuid()}:{os.getgid()} /target",
        ],
        capture_output=True,
        text=True,
        errors="replace",
    )
    if result.returncode != 0:
        raise ComposeError(
            "could not return a rollout path to the host uid for cleanup: "
            f"{(result.stderr or result.stdout).strip()}"
        )


def _claude_code_agent_extras(
    eval_dir: EvalDir, *, agent_image: str = AGENT_IMAGE
) -> tuple[list[str], dict[str, str]]:
    """Create and mount a fresh minimal Claude home for one rollout.

    The host profile is credential/config INPUT only. It is never mounted into
    the agent and therefore cannot receive memory, session, project-note or
    history writes from a rollout. OAuth refreshes land in the per-eval copy.
    """
    work_env = os.environ.get("IB_CLAUDE_WORK", "").strip()
    creds_env = os.environ.get("IB_CLAUDE_CREDENTIALS", "").strip()
    work_dir = Path(work_env).expanduser() if work_env else Path.home() / ".claude-work"
    creds = Path(creds_env).expanduser() if creds_env else work_dir / ".credentials.json"
    if not work_dir.is_dir():
        raise ComposeError(
            f"Claude Code config dir not found: {work_dir} "
            "(set IB_CLAUDE_WORK to the host directory that holds .claude.json)"
        )
    _seed_claude_credentials_from_macos_keychain(work_dir, creds)
    if not _valid_claude_oauth_file(creds):
        raise ComposeError(f"invalid Claude Code credentials file: {creds}")
    credential_data = json.loads(creds.read_text(encoding="utf-8"))
    eval_dir.register_runtime_secrets(*_credential_values(credential_data))

    isolated = eval_dir.root / "agent-home" / "claude"
    isolated.mkdir(parents=True, exist_ok=False)
    shutil.copy2(creds, isolated / ".credentials.json")
    # Never copy either host file. .claude.json is accumulated machine,
    # account, project and session state; settings.json may select models,
    # plugins, hooks and host commands. Fixed files make every rollout start
    # from the same non-interactive configuration.
    (isolated / ".claude.json").write_text(
        json.dumps(_CLAUDE_ROLLOUT_STATE, indent=2) + "\n", encoding="utf-8"
    )
    (isolated / "settings.json").write_text(
        json.dumps(_CLAUDE_ROLLOUT_SETTINGS, indent=2) + "\n", encoding="utf-8"
    )
    forbidden = ("projects", "memory", "history.jsonl", "sessions", "plans", "todos")
    leaked = [name for name in forbidden if (isolated / name).exists()]
    if leaked:
        raise ComposeError(f"isolated Claude home unexpectedly contains state: {leaked}")
    _make_claude_path_container_owned(isolated, agent_image)
    volumes = [f"{isolated.resolve()}:{_CLAUDE_CONFIG_IN_CONTAINER}"]
    env = {
        "CLAUDE_CONFIG_DIR": _CLAUDE_CONFIG_IN_CONTAINER,
        "HOME": _CLAUDE_AGENT_HOME,
    }
    return volumes, env


def _valid_codex_auth_file(path: Path) -> tuple[bool, str]:
    """``(ok, mode)`` for a Codex ``auth.json``.

    Two shapes are legitimate and the distinction matters for reporting: a
    ChatGPT-plan login carries ``tokens.access_token`` + ``tokens.refresh_token``
    (mode ``chatgpt``), while automation may instead carry a bare
    ``OPENAI_API_KEY`` (mode ``api_key``) — which bills per token rather than
    against a subscription. A lane that silently accepted either would make
    "this sweep cost nothing" unverifiable after the fact.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, json.JSONDecodeError):
        return False, ""
    if not isinstance(data, dict):
        return False, ""
    tokens = data.get("tokens")
    if isinstance(tokens, dict) and tokens.get("access_token") and tokens.get("refresh_token"):
        return True, "chatgpt"
    if data.get("OPENAI_API_KEY"):
        return True, "api_key"
    return False, ""


def codex_auth_mode() -> str:
    """``"chatgpt"``, ``"api_key"``, or ``""`` for the configured Codex home.

    Recorded in the rollout's meta.json so the billing path is a fact on disk
    rather than something inferred later from an absent cost figure.
    """
    home_env = os.environ.get("IB_CODEX_HOME", "").strip()
    home_dir = Path(home_env).expanduser() if home_env else Path.home() / ".codex"
    return _valid_codex_auth_file(home_dir / "auth.json")[1]


def _codex_agent_extras(
    eval_dir: EvalDir, *, agent_image: str = CODEX_AGENT_IMAGE
) -> tuple[list[str], dict[str, str]]:
    """Create and mount a fresh minimal Codex home for one rollout.

    Host path (override with env):
    - ``IB_CODEX_HOME`` (default ``~/.codex``) -> ``/codex-home``

    No macOS Keychain step: Codex persists its OAuth tokens in
    ``$CODEX_HOME/auth.json`` on every platform, and that file is explicitly
    host-portable. The Claude lane's ``_seed_claude_credentials_from_macos_keychain``
    has no counterpart here.
    """
    home_env = os.environ.get("IB_CODEX_HOME", "").strip()
    home_dir = Path(home_env).expanduser() if home_env else Path.home() / ".codex"
    if not home_dir.is_dir():
        raise ComposeError(
            f"Codex config dir not found: {home_dir} "
            "(set IB_CODEX_HOME to the host directory that holds auth.json)"
        )
    auth = home_dir / "auth.json"
    ok, mode = _valid_codex_auth_file(auth)
    # Fail loud and BEFORE Docker starts. The Claude lane learned this the
    # expensive way: an unauthenticated agent still produces a complete-looking
    # rollout that grades 0.0, so a whole sweep reads as a catastrophically bad
    # model rather than a broken lane.
    if not ok:
        raise ComposeError(
            f"Codex credentials missing or unreadable: {auth}\n"
            f"  authenticate the host once with:  CODEX_HOME={home_dir} codex login\n"
            "  (headless host? use `codex login --device-auth`)"
        )
    auth_data = json.loads(auth.read_text(encoding="utf-8"))
    eval_dir.register_runtime_secrets(*_credential_values(auth_data))
    isolated = eval_dir.root / "agent-home" / "codex"
    isolated.mkdir(parents=True, exist_ok=False)
    shutil.copy2(auth, isolated / "auth.json")
    (isolated / "auth.json").chmod(0o600)
    # Do not import host config, memories, sessions, histories, skills, MCP
    # servers, shell snapshots, or project state. Model and xhigh effort are
    # explicit CLI/provenance inputs; an empty fixed config is sufficient.
    (isolated / "config.toml").write_text("", encoding="utf-8")
    forbidden = ("memories", "sessions", "history.jsonl", "skills", "projects")
    leaked = [name for name in forbidden if (isolated / name).exists()]
    if leaked:
        raise ComposeError(f"isolated Codex home unexpectedly contains state: {leaked}")
    _make_claude_path_container_owned(isolated, agent_image)
    volumes = [f"{isolated.resolve()}:{_CODEX_HOME_IN_CONTAINER}"]
    env = {
        "CODEX_HOME": _CODEX_HOME_IN_CONTAINER,
        "HOME": _CLAUDE_AGENT_HOME,
        # Recorded so meta.json can state which billing path a rollout used
        # rather than leaving it to be inferred from cost being absent.
        "IB_CODEX_AUTH_MODE": mode,
    }
    return volumes, env


def _opencode_model_config(model: str) -> dict[str, object]:
    """Register an exact model through the credential-isolating gateway."""
    concrete = model.removeprefix("openrouter/").strip()
    if not concrete:
        raise ComposeError("OpenCode requires a non-empty OpenRouter model id")
    return {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            "openrouter": {
                "options": {
                    "apiKey": "integration-bench-provider-gateway",
                    "baseURL": _PROVIDER_GATEWAY_URL,
                },
                "models": {concrete: {}},
            }
        },
    }


def _opencode_agent_extras(
    eval_dir: EvalDir,
    *,
    model: str | None = None,
    agent_image: str = OPENCODE_AGENT_IMAGE,
) -> tuple[list[str], dict[str, str], str]:
    """Provision a fresh OpenCode home and gateway-only credential input.

    The operator's `.secrets/.env` is input-only.  It is never mounted and its
    value is never available to the participant container or rendered into
    compose.yaml, logs, provenance, or evidence.
    """
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise ComposeError("OPENROUTER_API_KEY is missing; put it in .secrets/.env or export it")
    eval_dir.register_runtime_secrets(key)
    isolated = eval_dir.root / "agent-home" / "opencode"
    isolated.mkdir(parents=True, exist_ok=False)
    provider_auth = eval_dir.root / "provider-auth"
    provider_auth.mkdir(mode=0o700)
    secret_env = provider_auth / "provider.env"
    fd = os.open(secret_env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(f"OPENROUTER_API_KEY={key}\n")
    for name in ("data", "config", "cache", "state"):
        (isolated / name).mkdir()
    if model:
        config_dir = isolated / "config" / "opencode"
        config_dir.mkdir()
        (config_dir / "opencode.json").write_text(
            json.dumps(_opencode_model_config(model), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    _make_claude_path_container_owned(isolated, agent_image)
    volumes = [f"{isolated.resolve()}:{_OPENCODE_HOME_IN_CONTAINER}"]
    env = {
        "HOME": _CLAUDE_AGENT_HOME,
        "XDG_DATA_HOME": f"{_OPENCODE_HOME_IN_CONTAINER}/data",
        "XDG_CONFIG_HOME": f"{_OPENCODE_HOME_IN_CONTAINER}/config",
        "XDG_CACHE_HOME": f"{_OPENCODE_HOME_IN_CONTAINER}/cache",
        "XDG_STATE_HOME": f"{_OPENCODE_HOME_IN_CONTAINER}/state",
        "OPENCODE_DISABLE_AUTOUPDATE": "true",
        "OPENCODE_DISABLE_DEFAULT_PLUGINS": "true",
        "OPENCODE_DISABLE_CLAUDE_CODE": "true",
        "OPENCODE_DISABLE_LSP_DOWNLOAD": "true",
        "OPENCODE_AUTO_SHARE": "false",
    }
    return volumes, env, str(secret_env.resolve())


def task_compose_unit_ready(task_dir: Path) -> bool:
    """Return whether every declared vendor has a standalone locked project."""
    try:
        task = load_task_config(Path(task_dir))
        entries = load_images_lock()["vendors"]
    except Exception:
        return False
    return bool(task.vendors) and all(meta.product in entries for meta in task.vendors.values())


def agent_dockerfile() -> Path:
    return Path(__file__).resolve().parent.parent / "docker" / "agent" / "Dockerfile"


def codex_agent_dockerfile() -> Path:
    return Path(__file__).resolve().parent.parent / "docker" / "agent-codex" / "Dockerfile"


def opencode_agent_dockerfile() -> Path:
    return Path(__file__).resolve().parent.parent / "docker" / "agent-opencode" / "Dockerfile"


def ensure_agent_image(
    *,
    tag: str = AGENT_IMAGE,
    force: bool = False,
    dockerfile: Path | None = None,
) -> str:
    """Build ``ib-agent:local`` from the ubuntu Dockerfile if missing or stale.

    Stale = image label ``ib.agent.dockerfile_sha`` does not match the current
    Dockerfile bytes (so apt/python bumps rebuild without manual ``docker rmi``).
    Set ``IB_AGENT_REBUILD=1`` or ``force=True`` to always rebuild.

    ``dockerfile`` selects the source for an alternate lane (the codex agent
    image). It must be paired with a distinct ``tag``: the staleness label is
    per-image, so pointing two Dockerfiles at one tag would make each lane
    rebuild the other's image on every run.
    """
    import hashlib

    df = Path(dockerfile) if dockerfile is not None else agent_dockerfile()
    if not df.is_file():
        raise ComposeError(f"agent Dockerfile missing: {df}")
    sha = hashlib.sha256(df.read_bytes()).hexdigest()[:16]
    force = force or os.environ.get("IB_AGENT_REBUILD", "").strip() in ("1", "true", "yes")
    locked = os.environ.get("IB_AGENT_IMAGE_LOCKED", "").strip() in ("1", "true", "yes")

    # Official VM campaigns pre-pull an immutable digest.  Such an image is
    # already content-addressed and must neither depend on our local build
    # label nor silently fall back to a mutable workstation build.
    if locked:
        insp = subprocess.run(
            ["docker", "image", "inspect", tag],
            capture_output=True,
            text=True,
            errors="replace",
        )
        if insp.returncode != 0:
            raise ComposeError(f"locked agent image is not present locally: {tag}")
        return tag

    if not force:
        insp = subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                tag,
                "--format",
                '{{index .Config.Labels "ib.agent.dockerfile_sha"}}',
            ],
            capture_output=True,
            text=True,
            errors="replace",
        )
        if insp.returncode == 0 and (insp.stdout or "").strip() == sha:
            return tag

    proc = subprocess.run(
        [
            "docker",
            "build",
            "-t",
            tag,
            "--label",
            f"ib.agent.dockerfile_sha={sha}",
            "-f",
            str(df),
            str(df.parent),
        ],
        capture_output=True,
        text=True,
        errors="replace",
    )
    if proc.returncode != 0:
        raise ComposeError(
            f"docker build {tag} failed:\n{(proc.stderr or proc.stdout or '')[-1200:]}"
        )
    return tag


def build_agent_images(
    harnesses: list[str] | None = None, *, force: bool = False
) -> dict[str, str]:
    """Build the local agent images needed by selected evaluation harnesses.

    The direct provider loop and Claude Code share the general tool image;
    Codex and OpenCode remain independently pinned images so one lane cannot
    change another lane's runtime during a campaign.
    """
    selected = harnesses or ["direct", "claude-code", "codex", "opencode"]
    specs = {
        "direct": (AGENT_IMAGE, agent_dockerfile()),
        "claude-code": (AGENT_IMAGE, agent_dockerfile()),
        "codex": (CODEX_AGENT_IMAGE, codex_agent_dockerfile()),
        "opencode": (OPENCODE_AGENT_IMAGE, opencode_agent_dockerfile()),
    }
    unknown = sorted(set(selected) - set(specs))
    if unknown:
        raise ComposeError(f"unknown agent harness(es): {', '.join(unknown)}")

    built_by_tag: dict[str, str] = {}
    result: dict[str, str] = {}
    for harness in selected:
        tag, dockerfile = specs[harness]
        if tag not in built_by_tag:
            built_by_tag[tag] = ensure_agent_image(
                tag=tag,
                force=force,
                dockerfile=dockerfile,
            )
        result[harness] = built_by_tag[tag]
    return result


def _seed_envs_from_task_contract(
    task_dir: Path, vendor_names: list[str]
) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {n: {} for n in vendor_names}
    task_path = task_dir / "task.yaml"
    if task_path.is_file():
        task_data = yaml.safe_load(task_path.read_text(encoding="utf-8")) or {}
        roles = ((task_data.get("contract") or {}).get("runtime") or {}).get("vendor_roles") or {}
        for name in vendor_names:
            env = (roles.get(name) or {}).get("environment") or {}
            out[name].update({str(k): str(v) for k, v in env.items()})
    return out


def _app_env_from_task_contract(task_dir: Path) -> dict[str, str]:
    """Return task-authored application runtime environment.

    ComposeUnitStack replaces the task's ``app`` service rather than layering
    on top of it.  Preserve business inputs such as cutoff timestamps, tenant
    identifiers, and input-file paths; harness-owned routing, credentials and
    storage paths are merged later and deliberately take precedence.
    """
    task_path = task_dir / "task.yaml"
    if task_path.is_file():
        task_data = yaml.safe_load(task_path.read_text(encoding="utf-8")) or {}
        env = ((task_data.get("contract") or {}).get("participant") or {}).get(
            "runtime_environment"
        )
        if isinstance(env, dict):
            return {str(k): str(v) for k, v in env.items() if v is not None}
    return {}


def _persistent_mount_parts(mount: Any) -> tuple[str, str]:
    """Validate one logical task volume without accepting a host path."""
    if isinstance(mount, str):
        parts = mount.split(":")
        if len(parts) != 2:
            raise ComposeError(f"invalid persistent volume mount: {mount!r}")
        source, target = parts
    elif isinstance(mount, dict):
        if mount.get("type") != "volume":
            raise ComposeError(f"persistent mounts must use logical volumes: {mount!r}")
        source = str(mount.get("source") or "")
        target = str(mount.get("target") or "")
    else:
        raise ComposeError(f"invalid persistent volume mount: {mount!r}")

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", source):
        raise ComposeError(f"persistent mount source must be a named volume: {source!r}")
    target_path = PurePosixPath(target)
    if not target.startswith(("/app/", "/data/")) or ":" in target or ".." in target_path.parts:
        raise ComposeError(f"persistent mount target must stay under /app/ or /data/: {target!r}")
    return source, target


def _app_volumes_from_task_contract(
    task_dir: Path,
) -> tuple[list[Any], dict[str, Any]]:
    """Return validated logical app mounts and their declarations.

    The harness synthesizes its app service, but multi-pass tasks rely on
    persistent storage between separate ``compose run`` calls. Runtime maps
    these logical names to unique eval-local paths; this parser never accepts
    an author-selected host path.
    """
    task_path = task_dir / "task.yaml"
    if task_path.is_file():
        task_data = yaml.safe_load(task_path.read_text(encoding="utf-8")) or {}
        participant = (task_data.get("contract") or {}).get("participant") or {}
        if "persistent_mounts" in participant:
            mounts = list(participant.get("persistent_mounts") or [])
            declarations: dict[str, Any] = {}
            for mount in mounts:
                source, _target = _persistent_mount_parts(mount)
                declarations[source] = None
            return mounts, declarations
    return [], {}


def _app_serve_port(task_dir: Path) -> int:
    task_path = task_dir / "task.yaml"
    if task_path.is_file():
        task_data = yaml.safe_load(task_path.read_text(encoding="utf-8")) or {}
        env = ((task_data.get("contract") or {}).get("participant") or {}).get(
            "runtime_environment"
        ) or {}
        if env.get("SERVE_PORT"):
            return int(env["SERVE_PORT"])
    return 4000


def _byte_size(value: Any) -> int:
    """Parse a positive task byte-size declaration into an integer."""
    raw = str(value).strip()
    suffixes = {
        "kib": 1024,
        "mib": 1024**2,
        "gib": 1024**3,
        "tib": 1024**4,
        "kb": 1000,
        "mb": 1000**2,
        "gb": 1000**3,
        "tb": 1000**4,
    }
    lowered = raw.lower()
    for suffix, multiplier in suffixes.items():
        if lowered.endswith(suffix):
            number = raw[: -len(suffix)].strip()
            try:
                parsed = float(number)
            except ValueError:
                break
            if parsed <= 0:
                break
            return int(parsed * multiplier)
    raise ComposeError(f"unsupported positive byte-size value in task contract: {value!r}")


def _compose_byte_size(value: Any) -> str:
    """Translate the contract's byte spelling to Docker's size syntax."""
    size = _byte_size(value)
    for divisor, suffix in ((1024**4, "t"), (1024**3, "g"), (1024**2, "m"), (1024, "k")):
        if size % divisor == 0:
            return f"{size // divisor}{suffix}"
    return str(size)


def _retained_size(paths: list[Path]) -> int:
    """Return logical bytes below trusted rollout-local roots, without links."""
    total = 0
    seen: set[tuple[int, int]] = set()
    for base in paths:
        if not base.exists() or base.is_symlink():
            continue
        for root, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(root) / name).is_symlink()]
            for name in files:
                path = Path(root) / name
                try:
                    stat = path.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                key = (stat.st_dev, stat.st_ino)
                if key in seen:
                    continue
                seen.add(key)
                total += stat.st_size
    return total


def _participant_resource_policy(task: TaskConfig) -> dict[str, Any]:
    """Compose limits for the candidate-controlled service in either phase.

    Work and grade stacks never run the agent and application concurrently, so
    the same task-declared participant budget applies to whichever service is
    candidate-controlled in that phase. The writable temporary filesystem is
    bounded by the declared disk budget; durable output/state mounts are
    checked by the grading artifact boundary.
    """
    limits = ((task.raw.get("contract") or {}).get("runtime") or {}).get("limits") or {}
    try:
        cpus = float(limits["cpus"])
        memory = _compose_byte_size(limits["memory"])
        disk = _compose_byte_size(limits["disk"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ComposeError(
            f"task {task.id} has invalid contract.runtime.limits: {limits!r}"
        ) from exc
    if cpus <= 0:
        raise ComposeError(f"task {task.id} must declare a positive CPU limit")
    return {
        "cpus": cpus,
        "mem_limit": memory,
        "pids_limit": _CONTAINER_PIDS_LIMIT,
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "init": True,
        "tmpfs": [f"/tmp:size={disk},mode=1777"],
    }


def _trusted_service_policy() -> dict[str, Any]:
    """Least-privilege defaults for vendor and egress infrastructure."""
    return {
        "cpus": _TRUSTED_SERVICE_CPUS,
        "mem_limit": _TRUSTED_SERVICE_MEMORY,
        "pids_limit": _CONTAINER_PIDS_LIMIT,
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "init": True,
        "tmpfs": ["/tmp:size=256m,mode=1777"],
    }


def _runtime_uid_gid() -> tuple[int, int]:
    """Return a non-root runtime identity compatible with host bind mounts."""
    uid, gid = os.getuid(), os.getgid()
    return (uid, gid) if uid != 0 else (1001, 1001)


def _make_runtime_path_writable(path: Path, uid: int, gid: int) -> None:
    """Provision eval-local bind mounts when the harness itself runs as root."""
    path.mkdir(parents=True, exist_ok=True)
    if os.getuid() != 0:
        return
    for root, dirs, files in os.walk(path):
        os.chown(root, uid, gid)
        for name in dirs:
            os.chown(Path(root) / name, uid, gid)
        for name in files:
            os.chown(Path(root) / name, uid, gid)


class ComposeUnitStack:
    """Canonical isolated task stack used by all supported harness workflows."""

    def __init__(
        self,
        task_dir: Path,
        eval_dir: EvalDir,
        *,
        app_repo: Path | None = None,
        include_agent: bool = False,
        agent_claude_code: bool = False,
        agent_codex: bool = False,
        agent_opencode: bool = False,
        opencode_model: str | None = None,
        startup_timeout_s: float = 120.0,
        ephemeral: bool = False,
    ) -> None:
        self.task_dir = Path(task_dir)
        self.eval_dir = eval_dir
        self.project = eval_dir.eval_id
        self.include_agent = include_agent
        self.agent_claude_code = agent_claude_code
        self.agent_codex = agent_codex
        self.agent_opencode = agent_opencode
        self.opencode_model = opencode_model
        if sum(bool(x) for x in (agent_claude_code, agent_codex, agent_opencode)) > 1:
            raise ComposeError(
                "agent harness modes are mutually exclusive: one "
                "container runs one harness, and results from the two lanes must "
                "never be pooled"
            )
        self.startup_timeout_s = startup_timeout_s
        self.ephemeral = ephemeral

        self.task: TaskConfig = load_task_config(self.task_dir)
        self.vendor_services = list(self.task.vendors.keys())
        self.vendor_service = self.vendor_services[0]
        self.app_service = "app"

        # Task roles are Compose service names; product IDs select images.
        # Alias tasks can therefore run e.g. vendor-legacy -> staffline without
        # adding a task-specific image or routing layer.
        self.vendor_products: dict[str, str] = {
            name: meta.product for name, meta in self.task.vendors.items()
        }
        self._blocks_by_product: dict[str, str] = {}
        for name, product in self.vendor_products.items():
            self._blocks_by_product.setdefault(product, name)

        self.vendor_envs: dict[str, dict[str, str]] = {
            name: {
                meta.checkpoint_env: str(meta.checkpoint),
                **{str(k): str(v) for k, v in meta.credentials.items()},
            }
            for name, meta in self.task.vendors.items()
        }
        seeded = _seed_envs_from_task_contract(self.task_dir, self.vendor_services)
        for name, env in seeded.items():
            self.vendor_envs[name].update(env)

        self.app_repo = (
            Path(app_repo).resolve()
            if app_repo
            else (eval_dir.repo if eval_dir.repo.is_dir() else None)
        )
        self._serve_port = _app_serve_port(self.task_dir)
        self._published_vendor_port: int | None = None
        self.participant_disk_limit_bytes = _byte_size(
            self.task.raw["contract"]["runtime"]["limits"]["disk"]
        )
        self._vendor_images = {
            name: resolve_vendor_image(self.product_of(name)) for name in self.vendor_services
        }
        if not include_agent:
            self._agent_image = (
                OPENCODE_AGENT_IMAGE
                if agent_opencode
                else CODEX_AGENT_IMAGE
                if agent_codex
                else AGENT_IMAGE
            )
        elif agent_opencode:
            self._agent_image = ensure_agent_image(
                tag=OPENCODE_AGENT_IMAGE, dockerfile=opencode_agent_dockerfile()
            )
        elif agent_codex:
            self._agent_image = ensure_agent_image(
                tag=CODEX_AGENT_IMAGE, dockerfile=codex_agent_dockerfile()
            )
        else:
            self._agent_image = ensure_agent_image()
        self._override_file: Path | None = None
        self.postgres_port: int | None = None
        self._canonical_host_db = eval_dir.canonical_data_dir / "canonical.db"
        self._started = False

        # data_ports filled after up() discovers published port
        self.data_ports: dict[str, int] = {}

    def product_of(self, service: str) -> str:
        """Resolve a block name, product name, or bare ``vendor`` to a product."""
        if service in self.vendor_products:
            return self.vendor_products[service]
        if service == "vendor":
            return self.vendor_products[self.vendor_service]
        return service

    def block_of(self, service: str) -> str:
        """Resolve a product name, block name, or bare ``vendor`` to a block name."""
        if service in self.vendor_products:
            return service
        if service == "vendor":
            return self.vendor_service
        return self._blocks_by_product.get(service, service)

    def is_vendor_service(self, service: str) -> bool:
        return (
            service in self.vendor_products
            or service in self._blocks_by_product
            or service == "vendor"
        )

    @property
    def vendor_env(self) -> dict[str, str]:
        return self.vendor_envs.setdefault(self.vendor_service, {})

    @vendor_env.setter
    def vendor_env(self, value: dict[str, str]) -> None:
        self.vendor_envs[self.vendor_service] = dict(value)

    @property
    def data_port(self) -> int | None:
        return self.data_ports.get(self.vendor_service)

    @property
    def database_url(self) -> str:
        return CANONICAL_DATABASE_URL

    @property
    def postgres_url(self) -> str | None:
        return None

    def reset_canonical_db(self) -> None:
        reset_db_file(self._canonical_host_db)

    def participant_disk_usage(self) -> int:
        """Measure every rollout-local path writable by candidate processes."""
        return _retained_size(
            [
                self.eval_dir.workspace,
                self.eval_dir.root / "agent-home",
                self.eval_dir.canonical_data_dir,
                self.eval_dir.participant_state_dir,
            ]
        )

    def assert_participant_disk_budget(self) -> int:
        """Fail closed when candidate-retained storage exceeds the task limit."""
        used = self.participant_disk_usage()
        if used > self.participant_disk_limit_bytes:
            raise ParticipantDiskLimitExceeded(
                f"participant disk limit exceeded: {used} > "
                f"{self.participant_disk_limit_bytes} bytes"
            )
        return used

    def data_base_url_for(self, service: str) -> str | None:
        block = self.block_of(service)
        port = self.data_ports.get(block)
        if port is None and block == self.vendor_service:
            port = self._published_vendor_port
        if not port:
            return None
        return f"http://localhost:{port}"

    @property
    def data_base_url(self) -> str | None:
        return self.data_base_url_for(self.vendor_service)

    def _rewrite_webhook_targets(self) -> None:
        reach = _APP_GATEWAY
        for env in self.vendor_envs.values():
            tgt = env.get("WEBHOOK_TARGET")
            if not tgt:
                continue
            parsed = urlparse(tgt)
            host = (parsed.hostname or "").lower()
            if host in (
                "connector",
                "vendor",
                "app",
                "127.0.0.1",
                "localhost",
                "host.docker.internal",
            ):
                env["WEBHOOK_TARGET"] = urlunparse(
                    parsed._replace(netloc=f"{reach}:{_APP_GATEWAY_PORT}")
                )

    def _write_vendor_cfg(self) -> None:
        """Write a trusted runtime snapshot and prepare per-role log dirs.

        This file is evidence only. It is never mounted into a vendor and is
        not an administrative control channel.
        """
        self._rewrite_webhook_targets()
        cfg: dict[str, Any] = {}
        for name, env in self.vendor_envs.items():
            product = self.product_of(name)
            if name not in self.task.vendors:
                # A scenario wrote vendor_envs under a key that is not a
                # declared vendor block — historically the literal compose
                # service name "vendor". That used to surface as a bare
                # KeyError here, crashing the grade to zero checks, which then
                # scored as an ordinary 0.0 and read as model failure. Name it.
                raise ComposeError(
                    f"vendor_envs has key {name!r}, which is not a declared "
                    f"vendor block for this task (declared: "
                    f"{sorted(self.task.vendors)}). A scenario likely assigned "
                    f"stack.vendor_envs['vendor'] directly; assign through the "
                    f"stack.vendor_env property instead so the block key is "
                    f"resolved."
                )
            host_log = self.eval_dir.vendor_logs_dir / name
            host_log.mkdir(parents=True, exist_ok=True)
            checkpoint = int(
                env.get(
                    "CHECKPOINT",
                    env.get(self.task.vendors[name].checkpoint_env, "0"),
                )
            )
            cfg[name] = {
                "vendor_id": product,
                "checkpoint": checkpoint,
                "env": {k: v for k, v in env.items() if k != "CHECKPOINT"},
                "log_dir": "/var/log/vendor",
            }
            cfg[name]["env"]["CHECKPOINT"] = str(checkpoint)
        self.eval_dir.vendor_cfg_file.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    def _vendor_url_env(self, *, for_container: bool) -> dict[str, str]:
        """URL env for app/agent. Inside compose network use service DNS."""
        out: dict[str, str] = {}
        for name in self.vendor_services:
            product = self.product_of(name)
            if for_container:
                url = f"http://{name}:8000"
            else:
                url = self.data_base_url_for(name) or "http://localhost"
            # Publish under BOTH the block name and the product. Alias tasks
            # declare the product form in their own compose (task-0025's app
            # reads GLOBALHIRE_BASE_URL, not VENDOR_LEGACY_BASE_URL); tasks
            # whose block IS the product get one key either way.
            for alias in dict.fromkeys((name, product)):
                prefix = alias.upper().replace("-", "_")
                out[f"{prefix}_BASE_URL"] = url
                out[f"{prefix}_DOCS_URL"] = f"{url}/_docs/"
            out.setdefault("VENDOR_BASE_URL", url)
            out.setdefault("VENDOR_DOCS_URL", f"{url}/_docs/")
        return out

    def _credential_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        for meta in self.task.vendors.values():
            env.update({str(k): str(v) for k, v in meta.credentials.items()})
        return env

    def render_compose(self) -> Path:
        """Write ``compose.yaml`` under the eval dir; return its path."""
        self._write_vendor_cfg()
        # __init__ already resolves/builds the lane-specific agent image. Do
        # not re-ensure the Claude default here: in locked VM mode that would
        # require an unrelated ib-agent:local even for the Codex lane.

        vendor_url_env = self._vendor_url_env(for_container=True)
        creds = self._credential_env()
        task_app_env = _app_env_from_task_contract(self.task_dir)
        task_app_volumes, _task_named_volumes = _app_volumes_from_task_contract(self.task_dir)
        participant_policy = _participant_resource_policy(self.task)
        trusted_service_policy = _trusted_service_policy()
        runtime_uid, runtime_gid = _runtime_uid_gid()

        # Logical task volumes are materialized as eval-local bind mounts. This
        # keeps multi-command persistence while making their bytes measurable,
        # isolated by eval id, and removable without touching unrelated Docker
        # volumes. Task authors never choose a host path.
        materialized_app_volumes: list[dict[str, Any]] = []
        for mount in task_app_volumes:
            source, target = _persistent_mount_parts(mount)
            state_dir = self.eval_dir.participant_state_dir / source
            _make_runtime_path_writable(state_dir, runtime_uid, runtime_gid)
            materialized_app_volumes.append(
                {"type": "bind", "source": str(state_dir.resolve()), "target": target}
            )

        services: dict[str, Any] = {}
        for name in self.vendor_services:
            log_dir = (self.eval_dir.vendor_logs_dir / name).resolve()
            _make_runtime_path_writable(log_dir, runtime_uid, runtime_gid)
            env = {
                **self.vendor_envs[name],
                "PORT": "8000",
                "REQUEST_LOG_PATH": "/var/log/vendor/requests.jsonl",
                "TOKEN_LOG_PATH": "/var/log/vendor/tokens.jsonl",
                "DELIVERY_LOG_PATH": "/var/log/vendor/deliveries.jsonl",
                "WEBHOOK_DELIVERY_LOG_PATH": "/var/log/vendor/webhook_deliveries.jsonl",
            }
            services[name] = {
                "image": self._vendor_images[name],
                # Image acquisition is an explicit setup/release operation.
                # A scored stack must fail if its digest is absent, and local
                # stacks must never turn a missing mutable tag into a registry
                # pull or a different artifact.
                "pull_policy": "never",
                "environment": env,
                "volumes": [f"{log_dir}:/var/log/vendor"],
                # Omit a published number and let Docker reserve an available
                # loopback port atomically. Host-side free-port probes have a
                # TOCTOU race under concurrent evaluations.
                "ports": [{"target": 8000, "host_ip": "127.0.0.1", "protocol": "tcp"}],
                # The workload network is the only path visible to candidate
                # code. A separate bridge lets the trusted host verifier use
                # published ports on Docker Desktop, which suppresses port
                # publishing for services attached only to an internal net.
                "networks": [_WORKLOAD_NETWORK, _VERIFIER_NETWORK],
                "user": f"{runtime_uid}:{runtime_gid}",
                **trusted_service_policy,
                "healthcheck": {
                    "test": [
                        "CMD",
                        "python3",
                        "-c",
                        "import urllib.request,sys; "
                        "sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/_ready').status==200 else 1)",
                    ],
                    "interval": "2s",
                    "timeout": "5s",
                    "retries": 30,
                    "start_period": "5s",
                },
            }

        vendor_dependencies = {
            name: {"condition": "service_healthy"} for name in self.vendor_services
        }
        services["app"] = {
            "build": {"context": str((self.app_repo or self.eval_dir.repo).resolve())},
            "environment": {
                **task_app_env,
                **vendor_url_env,
                **creds,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
                "DATABASE_URL": CANONICAL_DATABASE_URL,
                "OUTPUT_DIR": "/app/output",
                # A durable path for connectors that keep a FILE store
                # instead of the canonical sqlite DB. It has to sit under
                # the /data bind, because that is the only app mount that
                # survives across separate `docker compose run` invocations
                # -- and a task whose scenarios do `serve` in the service
                # container and then `dump` in a throwaway `run` container
                # needs exactly that.
                #
                # Measured on task-0049 (2026-08-07): its connector defaults
                # STATE_PATH to /app/state/store.json, the task's own
                # task contract declares an `app-state` persistent mount, and
                # this generator synthesises the app service from scratch.
                # Before that contract was retained, `serve` wrote a
                # full 201-placement store into its container layer and every
                # `run app dump` read an empty one, making every store check
                # in the task read rows=0 for gold. Verified by dumping inside
                # the serve container (201 rows) vs via `run` (0 rows).
                #
                # Inert for the other 49 tasks: they persist through
                # DATABASE_URL and never read STATE_PATH.
                "STATE_PATH": f"{Path(CANONICAL_DB_PATH).parent.as_posix()}/app_store.json",
                "SERVE_PORT": str(self._serve_port),
            },
            "volumes": [
                f"{(self.app_repo or self.eval_dir.repo).resolve() / 'output'}:/app/output",
                f"{self.eval_dir.canonical_data_dir.resolve()}:{Path(CANONICAL_DB_PATH).parent.as_posix()}",
                *materialized_app_volumes,
            ],
            # Candidate code runs here during grading. Keeping the app on
            # the same internal network as the vendor prevents a patch
            # from downloading a public solution/verifier at grade time.
            "networks": {
                _WORKLOAD_NETWORK: {
                    "aliases": ["app", "connector", _APP_GATEWAY],
                }
            },
            "depends_on": vendor_dependencies,
            # Build stages may run as root; candidate runtime code may not.
            # Bind mounts are owned by the invoking host user, which keeps
            # local and Linux-runner output writable without a root process.
            "user": f"{runtime_uid}:{runtime_gid}",
            **participant_policy,
        }

        if self.include_agent:
            agent_env = {
                **vendor_url_env,
                **creds,
                **_proxy_environment(self.vendor_services),
            }
            agent_volumes = [f"{self.eval_dir.workspace.resolve()}:/workspace"]
            provider_gateway_env_file: str | None = None
            if self.agent_claude_code:
                cc_volumes, cc_env = _claude_code_agent_extras(
                    self.eval_dir, agent_image=self._agent_image
                )
                agent_volumes.extend(cc_volumes)
                agent_env.update(cc_env)
            elif self.agent_codex:
                cx_volumes, cx_env = _codex_agent_extras(
                    self.eval_dir, agent_image=self._agent_image
                )
                agent_volumes.extend(cx_volumes)
                agent_env.update(cx_env)
            elif self.agent_opencode:
                oc_volumes, oc_env, provider_gateway_env_file = _opencode_agent_extras(
                    self.eval_dir,
                    model=self.opencode_model,
                    agent_image=self._agent_image,
                )
                agent_volumes.extend(oc_volumes)
                agent_env.update(oc_env)
            egress_hosts = _agent_egress_hosts(
                claude_code=self.agent_claude_code,
                codex=self.agent_codex,
                opencode=self.agent_opencode,
            )
            proxy_source = Path(__file__).resolve().with_name("egress_proxy.py")
            if not proxy_source.is_file():
                raise ComposeError(f"egress proxy source missing: {proxy_source}")
            services["egress-proxy"] = {
                "image": self._agent_image,
                "pull_policy": "never",
                "command": ["python3", _EGRESS_PROXY_IN_CONTAINER],
                "environment": {
                    "IB_EGRESS_ALLOWLIST": ",".join(egress_hosts),
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                "volumes": [
                    f"{proxy_source}:{_EGRESS_PROXY_IN_CONTAINER}:ro",
                ],
                "networks": [_WORKLOAD_NETWORK, _EGRESS_NETWORK],
                "user": "1001:1001",
                **trusted_service_policy,
                "healthcheck": {
                    "test": [
                        "CMD",
                        "python3",
                        "-c",
                        "import socket; s=socket.create_connection(('127.0.0.1',3128),2); "
                        "s.sendall(b'CONNECT health.invalid:443 HTTP/1.1\\r\\n\\r\\n'); "
                        "assert s.recv(32).startswith(b'HTTP/1.1 403')",
                    ],
                    "interval": "2s",
                    "timeout": "3s",
                    "retries": 15,
                    "start_period": "1s",
                },
            }
            if self.agent_opencode:
                if not provider_gateway_env_file:
                    raise ComposeError("OpenCode provider gateway credential input missing")
                provider_gateway_source = Path(__file__).resolve().with_name("provider_gateway.py")
                if not provider_gateway_source.is_file():
                    raise ComposeError(
                        f"provider gateway source missing: {provider_gateway_source}"
                    )
                services[_PROVIDER_GATEWAY_SERVICE] = {
                    "image": self._agent_image,
                    "pull_policy": "never",
                    "command": ["python3", _PROVIDER_GATEWAY_IN_CONTAINER],
                    "environment": {
                        "IB_PROVIDER_GATEWAY_PORT": str(_PROVIDER_GATEWAY_PORT),
                        "IB_PROVIDER_UPSTREAM": "https://openrouter.ai",
                        **(
                            {"IB_PROVIDER_ONLY": "google-vertex"}
                            if (self.opencode_model or "").startswith("google/gemini-")
                            else {}
                        ),
                        **(
                            {"IB_SANITIZE_GOOGLE_TOOL_OUTPUTS": "1"}
                            if (self.opencode_model or "").startswith("google/gemini-")
                            else {}
                        ),
                        "PYTHONDONTWRITEBYTECODE": "1",
                    },
                    "env_file": [provider_gateway_env_file],
                    "volumes": [
                        f"{provider_gateway_source}:{_PROVIDER_GATEWAY_IN_CONTAINER}:ro",
                    ],
                    "networks": [_WORKLOAD_NETWORK, _EGRESS_NETWORK],
                    "user": "1001:1001",
                    **trusted_service_policy,
                    "healthcheck": {
                        "test": [
                            "CMD",
                            "curl",
                            "-fsS",
                            f"http://127.0.0.1:{_PROVIDER_GATEWAY_PORT}/_health",
                        ],
                        "interval": "2s",
                        "timeout": "3s",
                        "retries": 15,
                        "start_period": "1s",
                    },
                }
            services["agent"] = {
                "image": self._agent_image,
                "pull_policy": "never",
                "command": ["sleep", "infinity"],
                "working_dir": "/workspace",
                "environment": agent_env,
                "volumes": agent_volumes,
                "networks": [_WORKLOAD_NETWORK],
                "user": "1001:1001",
                **participant_policy,
                "depends_on": {
                    **vendor_dependencies,
                    "egress-proxy": {"condition": "service_healthy"},
                    **(
                        {_PROVIDER_GATEWAY_SERVICE: {"condition": "service_healthy"}}
                        if self.agent_opencode
                        else {}
                    ),
                },
            }

        # Ensure app output dir exists for bind mount.
        out_dir = (self.app_repo or self.eval_dir.repo) / "output"
        _make_runtime_path_writable(out_dir, runtime_uid, runtime_gid)
        _make_runtime_path_writable(self.eval_dir.canonical_data_dir, runtime_uid, runtime_gid)

        doc = {
            "services": services,
            "networks": {
                # No default route: contestant-controlled agent/app processes
                # cannot bypass HTTP(S)_PROXY with curl, raw sockets or an IP.
                _WORKLOAD_NETWORK: {"internal": True},
                # Vendors are trusted benchmark services. Candidate app/agent
                # containers are deliberately not attached to this bridge.
                _VERIFIER_NETWORK: {},
            },
        }
        if self.include_agent:
            # Only the proxy is dual-homed onto this egress-capable bridge.
            doc["networks"][_EGRESS_NETWORK] = {}
        path = self.eval_dir.compose_file
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        return path

    def _base_cmd(self) -> list[str]:
        return [
            "docker",
            "compose",
            "-p",
            self.project,
            "-f",
            str(self.eval_dir.compose_file),
        ]

    def _run(self, cmd: list[str], check: bool = True, **kwargs) -> subprocess.CompletedProcess:
        result = subprocess.run(cmd, capture_output=True, text=True, errors="replace", **kwargs)
        if check and result.returncode != 0:
            raise ComposeError(
                f"command failed ({result.returncode}): {' '.join(cmd)}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result

    def _discover_published_port(self) -> int:
        for name in self.vendor_services:
            result = self._run(self._base_cmd() + ["port", name, "8000"])
            line = (result.stdout or "").strip().splitlines()[0]
            port = int(line.rsplit(":", 1)[-1])
            self.data_ports[name] = port
            product = self.product_of(name)
            if list(self.vendor_products.values()).count(product) == 1:
                self.data_ports[product] = port
        self._published_vendor_port = self.data_ports[self.vendor_service]
        return self._published_vendor_port

    def _wait_healthy(self) -> None:
        deadline = time.time() + self.startup_timeout_s
        last = ""
        while time.time() < deadline:
            try:
                # Refresh every role on each attempt. Compose can report one
                # published port before the other service has finished being
                # created; retaining that partial mapping made multi-vendor
                # startup poll ``None/_ready`` until the full timeout.
                self._discover_published_port()
                for name in self.vendor_services:
                    url = self.data_base_url_for(name)
                    if not url:
                        raise ComposeError(f"no published port for vendor role {name!r}")
                    wait_for_http(
                        f"{url}/_ready",
                        timeout_s=2.0,
                    )
                return
            except Exception as exc:  # noqa: BLE001
                last = str(exc)
                time.sleep(0.4)
        raise ComposeError(f"compose vendors not healthy: {last}")

    def build(self, service: str | None = None) -> None:
        if not self.eval_dir.compose_file.is_file():
            self.render_compose()
        cmd = self._base_cmd() + ["build"]
        if service:
            cmd.append(service)
        self._run(cmd)

    def up(
        self,
        *,
        exclude_app: bool = False,
        service: str | None = None,
        force_recreate: bool = False,
        build: bool = False,
    ) -> None:
        if self.include_agent:
            # Every lane uses the image's fixed uid 1001. Provision the
            # rollout-local workspace before startup so direct, Claude Code,
            # Codex, and OpenCode behave identically on native Linux binds.
            _make_claude_path_container_owned(self.eval_dir.workspace, self._agent_image)
        self.render_compose()

        if service and self.is_vendor_service(service):
            self.recreate_vendor(service)
            return

        cmd = self._base_cmd() + ["up", "-d"]
        if build:
            cmd.append("--build")
        if force_recreate:
            cmd.append("--force-recreate")

        if service:
            cmd.append(service)
        elif exclude_app:
            services = list(self.vendor_services)
            if self.include_agent:
                services.append("agent")
            cmd.extend(services)
        else:
            # Default up without starting app as a long-running service
            # (app runs via compose run). Still create network + vendors.
            services = list(self.vendor_services)
            if self.include_agent:
                services.append("agent")
            cmd.extend(services)

        self._run(cmd)
        self._started = True
        self._discover_published_port()
        self._wait_healthy()

    def recreate_vendor(self, service: str) -> None:
        block = self.block_of(service)
        if block not in self.vendor_services:
            raise ComposeError(f"unknown vendor service {service!r}")
        self.render_compose()
        self._run(self._base_cmd() + ["up", "-d", "--force-recreate", "--no-deps", block])
        self._started = True
        self._discover_published_port()
        wait_for_http(
            f"{self.data_base_url_for(block)}/_ready",
            timeout_s=self.startup_timeout_s,
            interval_s=0.2,
        )

    def exec(self, service: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        if self.is_vendor_service(service):
            service = self.block_of(service)
        return self._run(self._base_cmd() + ["exec", "-T", service, *args], check=check)

    def run(self, service: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        options = ["run", "--rm", "--no-deps"]
        cmd = self._base_cmd() + [*options, service, *args]
        result = self._run(cmd, check=check)
        self.assert_participant_disk_budget()
        return result

    # Service lifecycle is part of the canonical verifier stack contract. Every
    # call site guards the stop with `try/except` (raising in a `finally` would
    # mask the real error), which meant the failure was SILENT: the app service
    # was never stopped and the scenario carried on as if it had been.
    #
    # Measured 2026-08-07 on task-0049: `reset_store` stops the app service and
    # then wipes the connector's durable store. With `stop_service` missing, the
    # previous scenario's `serve` stayed alive, and its poll thread re-created the
    # store from memory seconds after the wipe — resurrecting a checkpoint-3 row
    # into a scenario asserting checkpoint-2 state, un-evictably (the store
    # upserts). Same class as the MegaGraderStack surface gap that hit 25 tasks.
    def start_service(self, service: str) -> None:
        if self.is_vendor_service(service):
            service = self.block_of(service)
        self._run(self._base_cmd() + ["start", service])

    def stop_service(self, service: str) -> None:
        if self.is_vendor_service(service):
            service = self.block_of(service)
        self._run(self._base_cmd() + ["stop", service])

    def restart_service(self, service: str) -> None:
        if self.is_vendor_service(service):
            service = self.block_of(service)
        self._run(self._base_cmd() + ["restart", service])

    def ps(self) -> list[dict]:
        result = self._run(self._base_cmd() + ["ps", "--format", "json"])
        out = (result.stdout or "").strip()
        if not out:
            return []
        try:
            data = json.loads(out)
            return data if isinstance(data, list) else [data]
        except json.JSONDecodeError:
            return [json.loads(line) for line in out.splitlines() if line.strip()]

    def down(self, *, volumes: bool = True) -> None:
        if not self.eval_dir.compose_file.is_file():
            return
        cmd = self._base_cmd() + ["down"]
        if volumes:
            cmd.append("-v")
        subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        self._started = False

    def logs(self, service: str | None = None) -> str:
        cmd = self._base_cmd() + ["logs", "--no-color"]
        if service:
            cmd.append(service)
        result = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        return (result.stdout or "") + (result.stderr or "")

    def read_vendor_jsonl(self, service: str, filename: str) -> list[dict[str, Any]]:
        path = self.eval_dir.vendor_logs_dir / self.block_of(service) / filename
        if not path.is_file():
            return []
        out: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    def cleanup_override_file(self) -> None:
        if self.ephemeral and not self._started:
            shutil.rmtree(self.eval_dir.root, ignore_errors=True)

    def agent_exec_stream(
        self,
        *args: str,
        on_line: "Callable[[str], None]",
        timeout_s: float | None = None,
        stdin_devnull: bool = False,
    ) -> int:
        """``docker compose exec agent …``, handing each output line to
        ``on_line`` as it arrives; returns the exit status.

        Long agent runs (Claude Code solving a task takes minutes) produce no
        observable progress under ``agent_exec``, which only returns once the
        command exits. stdout and stderr are merged so the log stays in
        chronological order.

        ``timeout_s`` is a REAL deadline, enforced by a watchdog thread that kills
        the process when it expires. It used to be passed only to
        ``proc.wait(timeout=...)`` below the streaming loop — which is unreachable
        until stdout hits EOF, i.e. until the process has already exited. So the
        host-side backstop could not fire during a long run at all; it bounded only
        the wait AFTER output stopped.

        Measured 2026-08-09: a task-0012 rollout ran **12,450s (3.5 hours)** against
        a 3,600s task timeout with a 3,720s host backstop. Neither fired — the
        in-container coreutils ``timeout`` sends SIGTERM, which the CLI did not die
        from, and this function's deadline was structurally unreachable. It consumed
        most of a night's scarce weekly budget for one rollout.
        """
        # `codex exec` treats a piped stdin as an appended `<stdin>` block even
        # when the prompt is passed as an argument, and blocks on it ("Reading
        # additional input from stdin..."). The driver runs rollouts from a
        # ThreadPoolExecutor whose stdin is whatever the supervisor inherited,
        # so leaving it attached makes the hang environment-dependent — the
        # worst kind. Callers that pass a prompt by argument opt out entirely.
        proc = subprocess.Popen(
            self._base_cmd() + ["exec", "-T", "agent", *args],
            stdin=subprocess.DEVNULL if stdin_devnull else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            bufsize=1,
        )
        timer: threading.Timer | None = None
        timed_out = threading.Event()

        def _kill_on_deadline() -> None:
            timed_out.set()
            # SIGKILL, not terminate: the whole point is that SIGTERM was ignored.
            try:
                proc.kill()
            except Exception:
                pass

        if timeout_s:
            timer = threading.Timer(float(timeout_s), _kill_on_deadline)
            timer.daemon = True
            timer.start()
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    on_line(line.rstrip("\n"))
            rc = proc.wait()
            if timed_out.is_set():
                raise subprocess.TimeoutExpired(cmd=args, timeout=timeout_s or 0)
            self.assert_participant_disk_budget()
            return rc
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        finally:
            if timer is not None:
                timer.cancel()
            if proc.stdout is not None:
                proc.stdout.close()

    def agent_exec(
        self, *args: str, check: bool = False, timeout_s: float | None = None
    ) -> subprocess.CompletedProcess:
        """``docker compose exec agent …`` for the tool sandbox.

        ``timeout_s`` is a host-side backstop; callers that need a hard wall
        (e.g. the Claude Code harness) should also wrap the in-container
        command with coreutils ``timeout`` so the child is actually killed.
        """
        result = self._run(
            self._base_cmd() + ["exec", "-T", "agent", *args],
            check=check,
            timeout=timeout_s,
        )
        self.assert_participant_disk_budget()
        return result


def stage_workspace_into_eval(task_dir: Path, eval_dir: EvalDir) -> Path:
    """Copy participant-allowed task files into the eval workspace."""
    from bench.workspace import prepare_rollout_workspace

    # prepare_rollout_workspace expects workspace_dir and writes repo/ under it
    if eval_dir.workspace.exists():
        for child in list(eval_dir.workspace.iterdir()):
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    return prepare_rollout_workspace(task_dir, eval_dir.workspace)
