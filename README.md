# AOP — Autonomous OpenRouter Pipeline

**One file. Standard library only. The scout writes the test first, and the test decides.**

*Repo directory is `sssf` for historical reasons; the tool is AOP. `spec.md` calls the
package `adw` and `description.md` calls it `aop-factory` — both are earlier names for
this same idea, kept for provenance, not current.*

```
scout ──▶ build ──▶ gate ──▶ review
            ▲         │
            └── fail ─┘   (3×, then revert the two files it touched)
```

A feature goal goes in. A scout model picks one file and writes a pytest module that
*is* the specification. A cheap builder model writes the implementation. Then Python —
not a model — decides whether it worked. On any failure the two files the run declared
are restored to their exact prior bytes and the run exits non-zero.

Nothing reaches the reviewer that hasn't passed a test the pipeline verified was
unchanged between generation and gate.

---

## Why this exists

Most agent loops ask a model to grade its own homework. This one doesn't:

| The usual way | AOP |
|---|---|
| Model writes code *and* its tests | Scout writes the contract **before** the builder runs; the builder cannot be aimed at that path, and the contract is hash-checked after every attempt |
| "Looks good to me" | `ast.parse` → the scouted test → your whole suite, with pytest's exit code respected |
| Pre-existing red tests block everything | Baseline diff — only failures *you introduced* count |
| Failed run leaves debris | Snapshot/restore of the declared files on **any** exit path, not just retry exhaustion |
| Model output → `open(path, "w")` | Paths validated as relative, in-repo, non-symlink, `.py`, outside dot-dirs, not protected config |

The last row matters more than it looks. The scout's `test_file` gets **executed** by pytest.
An unvalidated path there isn't a bug, it's a shell.

---

## Install

The controller is standard library only — no `requests`, no `pyyaml`, no `python-dotenv`.
The *runtime* is not: gates shell out to pytest, and generated code imports whatever your
project imports.

```bash
python3 -m pip install pytest   # into the interpreter you run aop.py with
cp .env.example .env            # add your key from openrouter.ai/keys
python3 aop.py --selftest       # no network; spawns pytest a handful of times
```

`aop.py` runs pytest as `sys.executable -m pytest`, so pytest must be importable by *that*
interpreter — an unrelated `pytest` on `PATH` is not enough. Python 3.9+ (`str.removeprefix`).

> **Note:** a real `OPENROUTER_API_KEY` exported in your shell **wins** over `.env`
> (same precedence as `python-dotenv`). If edits to `.env` seem ignored, that's why.

## Run

```bash
python3 aop.py "add a retry decorator with exponential backoff"
python3 aop.py --help
python3 aop.py --version
```

```
[*] scout: add a retry decorator with exponential backoff
    [nvidia/nemotron-3-super-120b-a12b:free] 412+1180 tok  (run total 1592)
[+] retry.py  (tests: tests/test_retry.py)
[~] 2 test(s) already failing; ignoring those
[*] build attempt 1/3
    [openai/gpt-oss-20b:free] 1620+740 tok  (run total 3952)
[!] gate failed:
    E   AttributeError: module 'retry' has no attribute 'retry'
[*] build attempt 2/3
    [openai/gpt-oss-20b:free] 2090+810 tok  (run total 6852)
[+] gate passed
    - ast.parse on retry.py: ok
    - pytest tests/test_retry.py: passed
    - full suite: no new failures vs a 2-failure baseline
    - build attempts: 2/3
    - not checked: lint, types, runtime behaviour outside the contract
[*] review

[=] left in the working tree, uncommitted: retry.py, tests/test_retry.py
```

Token counts are **usage**, not a cost ledger: they exclude retries that never returned a
body, local compute, and any provider-side pricing. `TOKEN_BUDGET` caps the whole run.

---

## The gate

Three stages, each cheaper than the one after it, none of them spending model tokens:

1. **`ast.parse`** — syntax. Catches truncated generations before anything is imported.
2. **The scouted test** — `pytest <test_file> --maxfail=1`. The contract.
3. **Baseline diff** — full suite, compared against the failures captured *before* any
   edit. Only newly-broken tests fail the gate.

Stage 3 is what stops the classic silent regression: the new test goes green, an existing
one goes red, and the reviewer is told everything passed.

Exit codes are part of stage 3's answer. pytest returns `0` pass, `1` tests failed, `5`
nothing collected; `2` (interrupted / collection error), `3` (internal) and `4` (usage)
mean the suite **did not run**. Counting zero `FAILED` lines from one of those is exactly
how a broken suite reads as green, so anything outside `{0, 1, 5}` aborts the run. Fixture
`ERROR` lines count as failures alongside `FAILED`.

## Safety properties

What is actually enforced, and where each one stops:

- **Containment** — `safe_path()` rejects absolute paths, `../` traversal, symlinks,
  non-`.py` files, dot-directories, `aop.py` / `conftest.py` / packaging config, and test
  files outside `tests/` or that pytest wouldn't collect.
- **Contract integrity** — the implementation and test paths must differ, the test path
  must not already exist, and the contract's bytes are re-checked after every build
  attempt. If executed code rewrites it, the run aborts and reverts.
- **Revert on any exit path** — the whole lifecycle sits in one `try`, so a provider
  error, a gate timeout, `Ctrl-C` or retry exhaustion all restore the same snapshot.
  **Limit:** the snapshot is the two declared files, held in memory. A crash between
  `write()` and `restore()` — `kill -9`, power loss — leaves the tree as it was mid-write.
  There is no journal.
- **Generated code is not sandboxed** — the runner environment is stripped of anything
  matching `KEY/TOKEN/SECRET/PASSWORD/CREDENTIAL/API/AUTH`, so your OpenRouter key does not
  reach pytest. That is a credential boundary, nothing more: generated tests still run as a
  normal child process with your filesystem and network. Code that writes outside the two
  declared files is *not* reverted.
- **No partial success** — a failed gate never reaches review. A reviewer transport
  failure or cancellation also restores both declared files and exits unsuccessfully.
  Broken code is never described as working.
- **Loud, not clever** — `null` content, a 200-with-error-body, a truncated generation or
  a reset connection each produce one clear line naming the model and provider.

> AOP leaves its result **uncommitted in your working tree**. It never stages, commits,
> pushes or deploys. `git diff` shows the change, but it is not an audit log — prompts,
> provider identity, usage and gate receipts are printed to the terminal and not persisted.

## Free-tier realities

The default roster is all `:free`, which means the network is part of the test surface:

- **Provider lottery.** OpenRouter load-balances free models across providers; some
  intermittently return `content: null`. `call()` re-rolls, which changes provider.
- **Upstream 429s.** A 3-attempt run is 5+ calls against a shared pool. Retryable statuses
  (408/409/425/429/5xx) back off exponentially, capped at 20s and 3 attempts; auth, quota
  and bad-model errors are fatal immediately with the body verbatim.
- **Truncation is fatal, not retried.** `finish_reason: length` means the answer hit
  `max_tokens`; re-rolling re-truncates. Raise the ceiling instead.
- **Not every model honors `response_format`.** OpenRouter silently drops unsupported
  parameters, so `parse_json()` tries raw → unfenced → first-`{`-to-last-`}`, and
  `validate_plan()` then checks types and size bounds locally. Structured-output support
  varies per model **and per endpoint**, so local validation is the real contract.

Swap the roster freely; re-check candidates with:

```bash
curl -s https://openrouter.ai/api/v1/models \
  | jq '.data[] | select(.pricing.prompt=="0") | select(.supported_parameters|index("structured_outputs")) | .id'
```

## Roster

| Role | Model | Why |
|---|---|---|
| `SCOUT` | `nvidia/nemotron-3-super-120b-a12b:free` | Large free model. Writes the plan *and* the tests. |
| `BUILDER` | `openai/gpt-oss-20b:free` | Fast, code-capable, only has to satisfy a fixed contract. |
| `REVIEWER` | `google/gemma-4-26b-a4b-it:free` | Instruction-tuned; reads the diff and the gate receipt. |

**Scout is the load-bearing slot.** It authors the tests, and the tests are the entire
contract — nothing downstream can catch a test that passes without actually specifying
the feature. If runs start going green on hollow tests, that's the model to upgrade, and
it's the one place paying for reasoning likely returns something.

---

## Deliberately not built

`description.md` and `spec.md` describe a considerably larger system: session
directories, typed JSON envelopes, a state-machine module, `roster.yaml`, per-model
fallback chains, cost telemetry. None of it is here, and their performance numbers
(including the 65–75% savings figure) are **unbenchmarked hypotheses**, not results.
This is a local tool for one person.

Also skipped, with the trigger to add each:

| Skipped | Add when |
|---|---|
| `ruff` in the gate | A style failure actually costs you something. Pre-existing lint debt would fail every build today. |
| Multi-file changes | A goal genuinely needs two files. Currently: one target, whole-file rewrite, no diff parsing. |
| Persisted run artifacts | You need to inspect a run after the terminal scrollback is gone. |
| Resume / patch export | A run costs enough that re-running it hurts. |
| Sandboxing | **Not** "when you point this at a repo you don't control" — the generated code is the untrusted input even in your own repo. Add it when a run will execute code you would not run by hand. Until then, run AOP in a disposable checkout. |

See [roadmap.md](roadmap.md) for milestone tracking and triggers, and [docs/architecture.md](docs/architecture.md) for detailed architecture, pipeline mechanics, and threat modeling.

## Tests

```bash
python3 aop.py --selftest
python3 -m unittest discover -s tests -v
```

The focused offline unit regressions cover reviewer failure/cancellation, exact-byte
restoration, successful retention and buffered write/restore failures using only
temporary synthetic files and failing in-memory streams. Writes are explicitly closed
before returning, so buffered I/O errors propagate instead of appearing successful.

The selftest runs in its own temporary git workspace — no fixed filenames in your tree, no discovery of
your tests. Covers `.env` precedence and credential scrubbing, fence stripping, JSON
recovery from prose, plan schema rejection, path/protected-file/test-root rejection, pytest
exit-code classification (collection error, fixture error), snapshot/restore in both
directions, the gate's pass/fail/syntax paths, CLI help and usage errors, and three
end-to-end `run()` paths with the provider stubbed: provider failure mid-build must leave
the original bytes intact, an implementation/contract path collision must abort before any
write, and the happy path must leave both files in place.

Also covers a stale-`.pyc` trap where two same-size rewrites within one second silently
test the *previous* attempt's bytecode.

No frameworks. It's `assert` and a `print("ok")`.
