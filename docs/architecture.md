# AOP Architecture & Safety Specification

> **Version:** 0.2  
> **Status:** Implemented in `aop.py`  
> **Target Audience:** Systems Architects and Operators

---

## 1. System Overview

AOP (Autonomous OpenRouter Pipeline) is a single-file, zero-dependency Python controller (`aop.py`) that implements a deterministic, test-driven code generation loop:

```
                  ┌──────────────────────────────┐
                  │  scout (Nemotron-120B:free)  │
                  └──────────────┬───────────────┘
                                 │
                     generates test contract
                     + targets 1 Python file
                                 │
                                 ▼
              ┌─────────▶┌──────────────────────────────┐
              │          │   build (GPT-OSS-20B:free)   │
              │          └──────────────┬───────────────┘
              │                         │
              │                  writes target
              │                         │
              │                         ▼
              │          ┌──────────────────────────────┐
   retry loop │          │      gate (deterministic)    │
   (max 3x)   │          │  1. ast.parse (syntax)       │
              │          │  2. pytest new test contract │
              │          │  3. pytest full suite diff   │
              │          └──────────────┬───────────────┘
              │                         │
              ├─── gate failed (≤3) ────┤
              │                         ├─── pass ────────┐
              │                         │                 │
              │                         ▼                 │
              │                  gate failed (>3)         │
              │                         │                 │
              │                 revert & abort            │
              │                                           ▼
              │                          ┌──────────────────────────────┐
              │                          │ review (Gemma-26B-IT:free)   │
              │                          │ unified diff + gate receipt  │
              │                          └──────────────┬───────────────┘
              │                                         │
              │                                 working tree left dirty
              │                                 (uncommitted for human)
```

---

## 2. Pipeline Stages

### 2.1 Scout Phase
- **Input:** User feature goal + tracked repository file list (`git ls-files`, capped at 300 entries).
- **Execution:** Calls OpenRouter `SCOUT` model (`nvidia/nemotron-3-super-120b-a12b:free`) with strict JSON schema instructions.
- **Output:** Validated JSON envelope containing:
  - `file`: Relative path to a single target implementation file.
  - `summary`: One-sentence summary of the planned change.
  - `test_file`: New relative path under `tests/` beginning with `test_`.
  - `test_code`: Complete pytest test module specifying feature requirements.
- **Validation:** Enforces string bounds (e.g. `file` ≤ 300 chars, `test_code` ≤ 200k chars), path containment, protected file rules, non-collision between `file` and `test_file`, and pre-existence checks.

### 2.2 Pre-Build Snapshot & Baseline
- Captures initial failing tests across the existing suite via `failing_tests()`.
- Captures binary snapshots of `file` (if it exists) and `test_file` (which must not exist).
- Unfences and writes `test_code` to `test_file`.

### 2.3 Build Phase
- **Input:** Feature goal, scout summary, current target contents (if modifying an existing file), test contract, and prior build gate error (on retries).
- **Execution:** Calls `BUILDER` model (`openai/gpt-oss-20b:free`).
- **Generation Safety:** The code is completely generated in memory *before* opening the target file for writing. If generation fails or aborts, existing files are never truncated.

### 2.4 Gate Phase (Deterministic Verification)
No model tokens are spent during verification. Python and pytest deterministically evaluate the code:
1. **AST Syntax Check (`ast.parse`):** Catches truncated outputs, syntax errors, and malformed statements before any code execution or imports.
2. **Contract Execution (`pytest <test_file> -q --maxfail=1`):** Verifies that the implementation satisfies the scouted contract.
3. **Regression Isolation (`failing_tests()`):** Runs the full test suite and diffs failed test IDs against the pre-build baseline. Only newly regressed tests cause a failure; pre-existing project test debt does not block the build.
4. **Contract Immutability Check:** Verifies that the byte content of `test_file` matches the original scout contract, preventing generated code from tampering with its own tests.
5. **Exit Code Classification:** Rejects pytest non-test failures (exit codes other than `0`, `1`, or `5`). Collection errors (code `2`) or framework crashes abort the run rather than being misread as green.

### 2.5 Review Phase
- Invoked **only** if the gate passes completely.
- Reads the actual `git`-style unified diff (up to 20k characters), the scout summary, the full test contract, and the concrete verification evidence receipt.
- Calls `REVIEWER` model (`google/gemma-4-26b-a4b-it:free`) to generate release notes and flag any untracked edge cases.

---

## 3. Safety Properties & Threat Model

| Property | Enforcement Mechanism | Failure Mode / Boundary |
|---|---|---|
| **Containment** | `safe_path()` checks relative paths, prevents escaping `ROOT`, forbids symlinks and dot-directories (`.git/`, `.github/`). | Prevents writes outside the repo tree. |
| **Protected Files** | `PROTECTED` set blocks targeting `aop.py`, `conftest.py`, `setup.py`, `pyproject.toml`, `pytest.ini`, `tox.ini`, `setup.cfg`. | Prevents model from modifying pipeline logic or pytest execution harness. |
| **Test Root Isolation** | `test_file` must reside under `tests/` and start with `test_`. | Ensures pytest collects only intended test modules. |
| **Contract Immutability** | Target path cannot equal test path; contract bytes verified after every build attempt. | Prevents self-grading / self-modifying test bypasses. |
| **Atomic-ish Revert** | In-memory binary snapshot of declared paths restored inside `try ... except BaseException`. | Cleans working tree on provider error, timeout, Ctrl-C, or gate exhaustion. **Limit:** Crash during write (`kill -9`, power loss) has no journal. |
| **Credential Boundary** | `runner_env()` scrubs environment variables matching `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `CREDENTIAL`, `API`, `AUTH`. | Prevents generated tests from reading `OPENROUTER_API_KEY` or system secrets via `os.environ`. |
| **Sandboxing Boundary** | Not sandboxed. Subprocess runs with current user privileges. | Tests can perform filesystem and network I/O permitted to the local user. Untrusted code should run in disposable containers. |
| **Token Budget** | `TOKEN_BUDGET = 400_000` caps cumulative tokens across all model calls. | Terminates runaway retry loops before unexpected token usage occurs. |
| **Transport Resilience** | Retries HTTP 408, 409, 425, 429, 5xx with exponential backoff (up to 20s); fatal on 401/402/404. | Mitigates transient free-tier rate limits without infinite stalls. |

---

## 4. Verification & Testing Architecture

AOP contains a built-in zero-dependency selftest suite executable via:
```bash
python3 aop.py --selftest
```

### Selftest Design:
- Runs inside an ephemeral `tempfile.TemporaryDirectory` with its own initialized git repository.
- Does not touch or inspect the host repository's test files.
- Stubs `call()` via `fake_call()` to verify:
  - `.env` parsing and credential stripping from the runner environment.
  - JSON repair from markdown fences and conversational prose.
  - Plan schema validation and string length enforcement.
  - Path traversal, symlink, dot-directory, and protected file rejection.
  - Pytest exit code classification (collection failure exit `2` vs fixture `ERROR`).
  - Stale `.pyc` prevention (`PYTHONDONTWRITEBYTECODE=1`).
  - Pre-generation write safety (no file clobber on provider failure).
  - Path collision rejection between target and contract.
  - Complete happy-path execution leaving files in place.
