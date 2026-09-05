# Integration-Bench

Integration-Bench measures whether AI coding agents can build, repair, harden,
and migrate production-style integrations against live, stateful APIs.

The public v1 suite contains 50 tasks across 15 deterministic fictional vendor
services. Tasks exercise polling, webhooks, authentication, pagination, retries,
reconciliation, write safety, partial failure, and recovery. Grading uses final
state and vendor-side evidence rather than repository tests alone.

## What is in this repository

- `harness/` — the `bench` CLI, isolated execution, grading, and tests.
- `tasks/public/` — all 50 public tasks and their reproducible verifiers.
- `contracts/` — versioned task, image, run-manifest, vendor, and verdict schemas.
- `images.lock.json` — immutable vendor image references used for scored runs.
- `docs/user-guide/` — setup and benchmark usage documentation.
- `releases/manifests/` — provenance and checksums for generated releases.

Vendor source, private task candidates, authoring solutions, internal reports,
and private evaluation infrastructure are intentionally not included.

## Quickstart

Requirements: Python 3.10+, Docker Engine with Compose v2, and Git.

```sh
python3 setup.py
./bench scoring-status --tasks-dir tasks/public --enforce
./bench validate-suite --tasks-dir tasks/public --enforce
```

Run an agent and grade its patch through one command:

```sh
./bench eval \
  --harness codex \
  --task tasks/public/task-0001 \
  --model gpt-5.6-terra
```

See [the quickstart](docs/user-guide/quickstart.md) for supported agent
harnesses, image modes, authentication, and artifact locations.

## Release model

This repository is an allowlisted export of HeyMilo's private canonical
monorepo. Every release manifest records the canonical source commit, task
catalog hash, image digests, contract versions, and file checksums. Public
releases have independent Git history and never inherit private Git objects.

## License

Copyright 2026 HeyMilo. See [LICENSE](LICENSE). No permission is granted beyond
the terms stated there.
