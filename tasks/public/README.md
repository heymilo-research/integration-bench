# Public tasks

All Integration-Bench tasks live here.

| Path | What |
|---|---|
| `task-NNNN/` | Canonical public task tree |

Vendor source lives under `vendors/` in the private canonical
monorepo. Tasks consume one locked image per vendor; participant workspaces do
not receive vendor source or task `authoring/` content.

The supported task and grading interface is `bench`; task-specific authoring
material remains private even when participant assets are exported publicly.
