# AGENTS.md

## Handoff First

Read `project_manifest.yaml`, `CLAUDE.md`, `memory.md`, and `AGENTS.md` if present before making changes. Keep `project_manifest.yaml` updated when paths, services, tables, scripts, or workflows change.

## Project Notes

This repo audits IMS biologic appointments and recent biologic dispense history for primary-insurance changes that may require PA/no-PA review.

Default to dry-run reports. Use reminder creation with `-Apply` only after reviewing candidate output and confirming the duplicate-suppression behavior is still appropriate.
