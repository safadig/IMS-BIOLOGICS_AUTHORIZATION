# AGENTS.md

## Handoff First

Read `project_manifest.yaml`, `CLAUDE.md`, `memory.md`, and `AGENTS.md` if present before making changes. Keep `project_manifest.yaml` updated when paths, services, tables, scripts, or workflows change.
## Parent Workspace Index

This repo is listed in the parent workspace repo at `C:\Users\safadig\Documents\GitHub` ([Master_Github_workspace](https://github.com/safadig/Master_Github_workspace)). If this project is added, renamed, moved, removed, published to a new remote, or its ownership/routing role changes, update the parent `workspace_manifest.yaml` and run `..\scripts\check-workspace-index.ps1` from the parent folder.

## Project Notes

This repo audits IMS biologic appointments and recent biologic dispense history for primary-insurance changes that may require PA/no-PA review.

Default to dry-run reports. Use reminder creation with `-Apply` only after reviewing candidate output and confirming the duplicate-suppression behavior is still appropriate.
