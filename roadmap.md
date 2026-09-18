# AOP Roadmap

> **Current Version:** v0.2  
> **Status:** Hardened Single-File Pipeline  
> **Last Updated:** September 2026

This roadmap tracks the development milestones of the Autonomous OpenRouter Pipeline (AOP). Features are pulled into implementation strictly when their concrete operational triggers are met.

---

## Completed Milestones

### [x] v0.1 — Proof of Concept Pipeline
- **Core Loop:** Linear scout → build → gate → review execution flow.
- **Unified OpenRouter Client:** Minimal standard library HTTP client with basic provider re-roll.
- **Deterministic Gate:** Syntax parsing with `ast.parse`, scouted pytest execution, and baseline regression diffing.
- **Basic Snapshot:** In-memory file snapshot and restore upon retry exhaustion.

### [x] v0.2 — Safety Hardening & Harness Isolation (Current)
- **Lifecycle Revert:** Unified `try ... except BaseException` block restoring snapshots on provider errors, process timeouts, `Ctrl-C`, or gate exhaustion.
- **Target Isolation:** Complete code generation in memory before opening file handles; zero truncation on mid-generation failure.
- **Contract Immutability:** Hash-verified contract preservation preventing generated test modification during gate runs.
- **Path & Containment Safety:** Strict containment checking preventing path traversal, symlink resolution, dot-directory penetration, and targeting protected config files (`aop.py`, `conftest.py`, packaging configs).
- **Test Runner Credential Scrubbing:** Sanitization of runner environment variables matching `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `CREDENTIAL`, `API`, `AUTH`.
- **Exit Code Classification:** Rejection of non-test pytest exit codes (`2` collection errors, `3` internal errors, `4` usage errors) to prevent falsely passing broken test suites.
- **Transport Resilience & Budget:** Exponential backoff for retryable HTTP errors (408/409/425/429/5xx), immediate failure for non-retryable 4xx codes, truncation detection (`finish_reason: length`), and global per-run token budget ceiling (`TOKEN_BUDGET = 400_000`).
- **Isolated Selftest Harness:** Self-contained temporary directory testing with ephemeral git roots, fake provider stubs, and regression checks covering F01 through F10 edge cases.
- **CLI Standardization:** Standard argument parsing via `argparse` supporting `--selftest`, `--version`, and `--help`.

---

## Future Milestones & Triggers

### [ ] v0.3 — Run Persistence & Patch Export
- **Trigger:** Terminal scrollback is no longer sufficient to inspect historical run receipts, prompt exchanges, and token usage breakdowns.
- **Scope:**
  - Persist run receipts, gate output logs, and raw diffs into `.aop/runs/<run_id>/`.
  - Export standalone patch files (`.patch`) for manual git application and review.

### [ ] v0.4 — Gate Extensibility & Linter Integration
- **Trigger:** Code style or typing issues introduce downstream friction, and projects have established linting configs.
- **Scope:**
  - Optional `ruff` syntax and formatting validation in Gate stage 1 (opt-in per repository to avoid failing on pre-existing project lint debt).
  - Baseline-diffed type checking (`mypy` or `pyright`) following the same debt-isolation model as pytest.

### [ ] v0.5 — Multi-File Target Atomic Changes
- **Trigger:** Feature goals frequently demand simultaneous changes across coupled implementation files (e.g. interface definition + implementation, or module + `__init__.py` export).
- **Scope:**
  - Scout envelope support for targeted multi-file manifest (bounded at 2–3 files).
  - Multi-file atomic snapshotting and write staging.

### [ ] v0.6 — Subprocess Sandboxing & Execution Isolation
- **Trigger:** AOP is executed against untrusted repositories or runs goals that require strict process and network isolation.
- **Scope:**
  - Containerized execution backend (Docker / Podman) or OS-level sandbox (macOS `sandbox-exec` / Linux bubblewrap).
  - Explicit read-only mounts for source repository with writable ephemeral output layers.
