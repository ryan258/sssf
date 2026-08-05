# AOP — Autonomous OpenRouter Pipeline

**One file. Zero dependencies. It writes the test first, and the test decides.**

```
scout ──▶ build ──▶ gate ──▶ review
            ▲         │
            └── fail ─┘   (3×, then revert everything)
```

A feature goal goes in. A scout model picks one file and writes a pytest module that
*is* the specification. A cheap builder model writes the implementation. Then Python —
not a model — decides whether it worked. If the gate is red three times, every byte the
pipeline touched is restored and the run exits non-zero.

Nothing reaches the reviewer that hasn't passed a test the builder was forbidden to edit.

---

## Why this exists

Most agent loops ask a model to grade its own homework. This one doesn't:

| The usual way | AOP |
|---|---|
| Model writes code *and* its tests | Scout writes tests **before** the builder runs; builder never gets a path to them |
| "Looks good to me" | `ast.parse` → the scouted test → your whole suite |
| Pre-existing red tests block everything | Baseline diff — only failures *you introduced* count |
| Failed run leaves debris | Byte-exact snapshot/restore of every file touched |
| Model output → `open(path, "w")` | Paths validated as relative, in-repo, non-symlink, `.py` |

The last row matters more than it looks. The scout's `test_file` gets **executed** by pytest.
An unvalidated path there isn't a bug, it's a shell.

---

## Install

There is nothing to install. `aop.py` is standard library only — no `requests`, no
`pyyaml`, no `python-dotenv`.

```bash
cp .env.example .env      # add your key from openrouter.ai/keys
python3 aop.py --selftest # no network, under a second
```

You need `pytest` on your `PATH`, which any repo worth pointing this at already has.

> **Note:** a real `OPENROUTER_API_KEY` exported in your shell **wins** over `.env`
> (same precedence as `python-dotenv`). If edits to `.env` seem ignored, that's why.

## Run

```bash
python3 aop.py "add a light/dark theme switcher with persistence"
```

```
[*] scout: add a light/dark theme switcher with persistence
    [nvidia/nemotron-3-super-120b-a12b:free] 412+1180 tok
[+] theme.py  (tests: tests/test_theme.py)
[~] 2 test(s) already failing; ignoring those
[*] build attempt 1/3
    [openai/gpt-oss-20b:free] 1620+740 tok
[!] gate failed:
    E   AttributeError: module 'theme' has no attribute 'load'
[*] build attempt 2/3
    [openai/gpt-oss-20b:free] 2090+810 tok
[+] gate passed
[*] review
```

Token counts print per call, so cost is observable rather than asserted.

---

## The gate

Three stages, each cheaper than the one after it, each free:

1. **`ast.parse`** — syntax. Catches truncated generations before anything is imported.
2. **The scouted test** — `pytest <test_file> --maxfail=1`. The contract.
3. **Baseline diff** — full suite, compared against the failures captured *before* any
   edit. Only newly-broken tests fail the gate.

Stage 3 is what stops the classic silent regression: the new test goes green, an existing
one goes red, and the reviewer is told everything passed.

## Safety properties

- **Containment** — `safe_path()` rejects absolute paths, `../` traversal, symlinks,
  non-`.py` files, and test files pytest wouldn't collect.
- **Atomic-ish revert** — `snapshot()` records the exact bytes (or absence) of every
  file the run can write. Total failure calls `restore()`: modified files return to
  their original content, created files are deleted.
- **No partial success** — a failed run raises `SystemExit` *before* the review call.
  Broken code is never described as working.
- **Loud, not clever** — a model returning `null` content, a 200-with-error-body, or a
  reset connection each produce one clear line naming the model and provider.

## Free-tier realities

The default roster is all `:free`, which means the network is part of the test surface:

- **Provider lottery.** OpenRouter load-balances free models across providers; some
  intermittently return `content: null`. `call()` re-rolls once, which changes provider.
- **Upstream 429s.** A 3-attempt run is 5+ calls against a shared pool. The error is
  surfaced verbatim, including OpenRouter's remedy link. There is deliberately no
  backoff — re-dialing a shared-pool rate limit immediately is theater.
- **Not every model honors `response_format`.** OpenRouter silently drops unsupported
  parameters, so `parse_json()` tries raw → unfenced → first-`{`-to-last-`}`. The scout
  slot works whether or not the model supports structured output.

Swap the roster freely; re-check candidates with:

```bash
curl -s https://openrouter.ai/api/v1/models \
  | jq '.data[] | select(.pricing.prompt=="0") | select(.supported_parameters|index("structured_outputs")) | .id'
```

## Roster

| Role | Model | Why |
|---|---|---|
| `SCOUT` | `nvidia/nemotron-3-super-120b-a12b:free` | Largest of the free set. Writes the plan *and* the tests. |
| `BUILDER` | `openai/gpt-oss-20b:free` | Fast, code-capable, only has to satisfy a fixed contract. |
| `REVIEWER` | `google/gemma-4-26b-a4b-it:free` | Instruction-tuned; produces the release notes. |

**Scout is the load-bearing slot.** It authors the tests, and the tests are the entire
contract — nothing downstream can catch a test that passes without actually specifying
the feature. If runs start going green on hollow tests, that's the model to upgrade, and
it's the one place paying for reasoning likely returns something.

---

## Deliberately not built

`description.md` and `spec.md` describe a considerably larger system: session
directories, typed JSON envelopes, a state-machine module, `roster.yaml`, per-model
fallback chains, cost telemetry. None of it is here. This is a local tool for one person
and `git diff` is the audit log.

Also skipped, with the trigger to add each:

| Skipped | Add when |
|---|---|
| `ruff` in the gate | A style failure actually costs you something. Pre-existing lint debt would fail every build today. |
| Multi-file changes | A goal genuinely needs two files. Currently: one target, whole-file rewrite, no diff parsing. |
| Retry/backoff logic | Reruns stop being good enough — then add your own provider key first. |
| Diff-based reviewer prompt | You want release notes describing *changes* rather than final state. |
| Sandboxing | You point this at a repo you don't control. It executes model-authored tests in your working directory. |

That last one is the real boundary. AOP assumes you trust the goal you typed and the
repo you're standing in.

## Tests

```bash
python3 aop.py --selftest
```

Covers `.env` parsing precedence, fence stripping, JSON recovery from prose, path
traversal rejection, snapshot/restore in both directions, and the gate's pass/fail/syntax
paths — including a stale-`.pyc` trap where two same-size rewrites within one second
silently test the *previous* attempt's bytecode.

No frameworks. It's `assert` and a `print("ok")`.
