# Agent working rules

## GitHub Actions budget

- Run relevant checks locally. Do not start GitHub Actions to validate routine edits, or rerun completed checks without a new failure or material change.
- Do not enable push or pull-request triggers for tests, recovery drills, or operational refreshes. These run manually when explicitly requested; production refreshes and backups retain their approved schedules.
- Batch related changes and publish once at the end of the task. Do not push after each small edit.
- Do not manually dispatch builds, APK generation, full data refreshes, or archive-producing workflows unless the user explicitly requests that run. Authorization to edit code does not authorize an extra paid verification run.
- Preserve necessary publication of changed site/function code. Inspect workflow triggers before pushing; do not publish documentation alone if it would trigger an unnecessary site build.
- Keep the AI Reader workflows in the manual mode configured by Claude. Read that repository's current AGENTS.md before working there.
