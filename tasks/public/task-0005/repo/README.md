# TalentForge connector

Keeps our canonical candidate store fresh from TalentForge using a **hybrid** of
webhooks (push, for freshness) and polling (pull, for reconciliation). See
`../PROBLEM.md` for the full ticket and `../docs/` for the vendor API.

## Commands

`connector sync` · `connector serve` · `connector dump` — see `../PROBLEM.md`.

## Tests

```
pip install -e '.[dev]'
pytest
```
