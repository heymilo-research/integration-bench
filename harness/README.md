# Harness

`bench` is the supported Integration-Bench command surface. The root wrapper
uses the environment installed by `setup.py`; `uv run --project harness bench`
is equivalent for development.

Each run gets a unique generated Compose project containing:

- one normal service per task vendor role;
- the candidate app;
- an isolated agent container when the selected lane needs one; and
- a fail-closed provider egress proxy.

Vendor images expose their API at the role URL and canonical documentation at
`/_docs/`. Checkpoint and fault changes recreate only the affected service.
There is no shared vendor process or participant-accessible admin surface.

## Commands

```sh
bench build-vendors [--vendor staffline]
bench build-agents [--harness codex]
bench pull [--vendor staffline]
bench run --task tasks/public/task-0001 --agent "..."
bench grade --task tasks/public/task-0001 --patch patch.diff
bench validate --task tasks/public/task-0001 --strict
bench validate-suite --tasks-dir tasks/public --enforce
bench grade-all --tasks-dir tasks/public
bench eval --harness direct --task tasks/public/task-0001 --model sonnet
bench eval --harness claude-code --task tasks/public/task-0001
bench eval --harness codex --task tasks/public/task-0001 --model gpt-5.6-terra
bench eval --harness opencode --task tasks/public/task-0001 \
  --model moonshotai/kimi-k2.5 --variant high
```

`direct` uses provider API credentials. `claude-code` and `codex` use their
subscription/CLI auth by default, with an optional explicit `--model`.
`opencode` uses OpenRouter and requires `--model`. Every lane uses the same
isolated task runtime and emits the same result shape.

Private source checkouts default to mutable standalone `<vendor>:local` images.
Public exports and scored/CI runs default to `IB_IMAGE_MODE=locked`; missing
immutable digests fail without fallback.

## Tests

```sh
uv run --project harness --extra dev pytest -q harness/tests
uv run --project harness bench scoring-status --tasks-dir tasks/public --enforce
uv run --project harness bench validate-suite --tasks-dir tasks/public --enforce
```

These checks are Docker-free. Use `bench pull` to verify access to the immutable
vendor images before an end-to-end run.

Participant workspace projection is allowlisted by `task.yaml`. It excludes
verifiers, fixtures, task metadata, authoring patches, and migration files.
Run manifests record task/catalog/image-lock hashes, resolved images, source
revision, protocol, harness, model, seed, and artifact hashes.
