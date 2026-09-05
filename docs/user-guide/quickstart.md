# Quickstart

Requirements: Python 3.10+, Docker Engine with Compose v2, and Git.

## Install and prepare images

```sh
python3 setup.py
```

The public repository does not contain vendor source. Setup therefore pulls the
immutable standalone vendor images recorded in `images.lock.json`, then builds
the selected agent-runtime images locally. Use `--skip-images` when you only
need the Docker-free CLI and tests.

## Check the public suite

```sh
./bench scoring-status --tasks-dir tasks/public --enforce
./bench validate-suite --tasks-dir tasks/public --enforce
```

These checks require no model API key and no private authoring solutions.

## Run an evaluation

Choose an agent runtime with one `eval` command:

```sh
./bench eval --harness direct --task tasks/public/task-0001 --model sonnet
./bench eval --harness claude-code --task tasks/public/task-0001
./bench eval --harness codex --task tasks/public/task-0001 --model gpt-5.6-terra
./bench eval --harness opencode --task tasks/public/task-0001 \
  --model moonshotai/kimi-k2.5 --variant high
```

`direct` and `opencode` require an explicit model. Claude Code and Codex may use
the model selected by their CLI configuration. Provider credentials belong in
the local `.env`; they are copied into isolated per-run agent homes and are not
included in participant workspaces or release evidence.

Public checkouts default to `IB_IMAGE_MODE=locked` because vendor source is not
included. Private source checkouts default to locally built images. Either mode
may be selected explicitly, but locked mode refuses mutable tags and missing
digests.

Outputs are written below the gitignored `artifacts/` directory.
