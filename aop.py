#!/usr/bin/env python3
"""Autonomous OpenRouter Pipeline: scout -> build -> gate -> review, one file at a time."""
import argparse, ast, difflib, json, os, re, subprocess, sys, tempfile, textwrap, time
import urllib.error, urllib.request

# All :free tier. Free tiers are rate-limited per day, so a long retry loop can 429 -- call()
# classifies the status and surfaces the body. Re-check the roster with:
#   curl -s https://openrouter.ai/api/v1/models | jq '.data[] | select(.pricing.prompt=="0")'
# Structured-output support varies per model and per endpoint; parse_json() does not rely on it.
SCOUT = "nvidia/nemotron-3-super-120b-a12b:free"
BUILDER = "openai/gpt-oss-20b:free"
REVIEWER = "google/gemma-4-26b-a4b-it:free"
VERSION = "0.2"
MAX_RETRIES = 3          # build attempts per run
MAX_CALL_ATTEMPTS = 3    # transport attempts per model call
TOKEN_BUDGET = 400_000   # whole-run ceiling; a stuck loop stops costing before you notice it
ERR_CHARS = 2000
DIFF_CHARS = 20_000
MAX_FILES = 300          # repo listing sent to the scout, truncated on a line boundary
GATE_TIMEOUT = 120
SUITE_TIMEOUT = 600

API = "https://openrouter.ai/api/v1/chat/completions"
FENCE = re.compile(r"^\s*```[a-zA-Z]*\n(.*?)\n```\s*$", re.S)
ROOT = os.path.realpath(".")   # rebound by selftest() inside its temp workspace
RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}
# Never let a plan name these: they decide what "the tests passed" means.
PROTECTED = {"aop.py", "conftest.py", "setup.py", "pyproject.toml", "pytest.ini", "tox.ini",
             "setup.cfg"}
SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "API", "AUTH")
TEST_ROOT = "tests"
_spent = 0  # tokens charged so far this run


def load_env(path=".env"):
    """ponytail: 6 lines beats a python-dotenv dep. Real environment wins, same as dotenv."""
    for line in open(path) if os.path.exists(path) else []:
        line = line.strip().removeprefix("export ")
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def runner_env():
    """Environment for generated code. Built per call, so .env settings loaded at startup reach
    the tests -- minus anything that looks like a credential.

    ponytail: this is a credential boundary, not a sandbox. Generated tests still run as a normal
    child process with your filesystem and network. For goals you would not run by hand, run aop
    itself inside a disposable container or VM.
    """
    env = {k: v for k, v in os.environ.items()
           if not any(h in k.upper() for h in SECRET_HINTS)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # same-size rewrites within one second reuse a stale .pyc
    return env


def call(model, system, user, json_mode=False, max_tokens=8192):
    global _spent
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is not set")
    if _spent >= TOKEN_BUDGET:
        raise SystemExit(f"[x] token budget exhausted ({_spent}/{TOKEN_BUDGET}); nothing more sent")
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        API, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    err = ""
    for attempt in range(MAX_CALL_ATTEMPTS):
        if attempt:
            time.sleep(min(2 ** attempt, 20))  # free tiers 429 in bursts; back off before re-dialing
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if e.code not in RETRYABLE:  # auth/quota/model: deterministic, body says why
                raise SystemExit(f"[x] OpenRouter {e.code} ({model}): {detail}")
            err = f"HTTP {e.code}: {detail}"
        except (urllib.error.URLError, TimeoutError) as e:  # reset mid-handshake: just re-dial
            err = f"transport: {e}"
        except json.JSONDecodeError as e:
            err = f"malformed response body: {e}"
        else:
            u = data.get("usage") or {}
            _spent += (u.get("total_tokens")
                       or (u.get("prompt_tokens") or 0) + (u.get("completion_tokens") or 0))
            print(f"    [{model}] {u.get('prompt_tokens', '?')}+{u.get('completion_tokens', '?')}"
                  f" tok  (run total {_spent})")
            if finish_reason(data) == "length":  # deterministic: retrying re-truncates
                raise SystemExit(f"[x] {model} hit max_tokens={max_tokens} mid-answer; raise it")
            text = content_of(data)
            if text:
                return text
            # OpenRouter can return 200 with an error body; without this it reads as "empty content"
            err = (json.dumps(data["error"])[:300] if data.get("error")
                   else f"empty content from {data.get('provider')!r}")
        print(f"    [{model}] {err}; retrying ({attempt + 1}/{MAX_CALL_ATTEMPTS})")
    raise SystemExit(f"[x] {model} failed {MAX_CALL_ATTEMPTS}x ({err}); rerun, or pick another model")


def content_of(data):
    """Free-tier providers intermittently return null content. '' means 'nothing usable'."""
    if not isinstance(data, dict):
        return ""
    choice = (data.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content")
    return text if isinstance(text, str) and text.strip() else ""


def finish_reason(data):
    choice = (data.get("choices") or [{}])[0]
    return choice.get("finish_reason") if isinstance(choice, dict) else None


def unfence(text):
    m = FENCE.match(text.strip())
    return (m.group(1) if m else text).strip() + "\n"


def parse_json(raw):
    """Not every model supports response_format; OpenRouter drops it silently. Dig the JSON out."""
    for candidate in (raw, unfence(raw), raw[raw.find("{"):raw.rfind("}") + 1]):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
    raise SystemExit(f"[x] scout returned no usable JSON:\n{raw[:500]}")


def validate_plan(plan):
    """A parseable envelope is not a valid plan. Check shape and bounds before any write."""
    if not isinstance(plan, dict):
        raise SystemExit(f"[x] scout plan is {type(plan).__name__}, expected a JSON object")
    for key, limit in (("file", 300), ("summary", 4000), ("test_file", 300), ("test_code", 200_000)):
        v = plan.get(key)
        if not isinstance(v, str) or not v.strip():
            raise SystemExit(f"[x] scout plan {key!r} must be a non-empty string, "
                             f"got {type(v).__name__}")
        if len(v) > limit:
            raise SystemExit(f"[x] scout plan {key!r} is {len(v)} chars, over the {limit} limit")
    return plan


def safe_path(p, kind):
    """Model output becomes a filesystem write and (for tests) gets executed. Contain it."""
    if not isinstance(p, str) or not p.strip() or not p.endswith(".py"):
        raise SystemExit(f"[x] scout {kind} is not a .py path: {p!r}")
    raw = os.path.join(ROOT, p)
    full = os.path.realpath(raw)
    if os.path.isabs(p) or not full.startswith(ROOT + os.sep):
        raise SystemExit(f"[x] scout {kind} escapes the repo: {p!r}")
    if os.path.islink(raw):
        raise SystemExit(f"[x] scout {kind} is a symlink: {p!r}")
    parts = os.path.relpath(full, ROOT).split(os.sep)
    if any(part.startswith(".") for part in parts):
        raise SystemExit(f"[x] scout {kind} points into a dot directory: {p!r}")
    if os.path.relpath(full, ROOT) in PROTECTED or parts[-1] in PROTECTED:
        raise SystemExit(f"[x] scout {kind} names a protected file: {p!r}")
    if kind == "test_file":
        if not parts[-1].startswith("test_"):
            raise SystemExit(f"[x] scout test_file is not collectable by pytest: {p!r}")
        if parts[0] != TEST_ROOT:
            raise SystemExit(f"[x] scout test_file must live under {TEST_ROOT}/: {p!r}")
    return full


def repo_files():
    r = subprocess.run(["git", "ls-files"], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"[x] `git ls-files` failed ({r.returncode}); aop needs a git repo "
                         f"for context:\n{r.stderr.strip()[:300]}")
    files = r.stdout.splitlines()
    listing = "\n".join(files[:MAX_FILES])
    if len(files) > MAX_FILES:  # disclosed, not silently severed at 4000 chars mid-path
        listing += f"\n...and {len(files) - MAX_FILES} more tracked file(s) not shown"
    return listing


def scout(goal):
    raw = call(
        SCOUT,
        "You are a lead engineer. Reply with JSON only, keys: file (one relative path to create or "
        f"modify), summary, test_file (NEW relative path under {TEST_ROOT}/, basename starting "
        "test_, and NOT the same path as file), test_code (complete pytest module that imports "
        "`file` and fully specifies the feature). "
        "Tests are the contract: they must pass only if the feature is correct.",
        f"Goal: {goal}\n\nRepo files:\n{repo_files()}",
        json_mode=True,  # honoured when the scout supports it, dropped otherwise; parse_json covers both
        max_tokens=8192,  # plan + a complete test module; 4096 truncated mid-JSON
    )
    plan = validate_plan(parse_json(raw))
    target = safe_path(plan["file"], "file")
    test_file = safe_path(plan["test_file"], "test_file")
    if target == test_file:  # otherwise the builder rewrites its own contract and always passes
        raise SystemExit(f"[x] scout aimed file and test_file at the same path: {plan['file']!r}")
    if os.path.exists(test_file):
        raise SystemExit(f"[x] scout test_file already exists: {plan['test_file']!r}")
    return plan, target, test_file


def build(goal, plan, current, error):
    prompt = f"Goal: {goal}\nPlan: {plan['summary']}\nWrite the complete contents of {plan['file']}."
    if current is not None:
        prompt += f"\n\nCurrent contents of {plan['file']}:\n{current}"
    prompt += f"\n\nIt must pass these tests, which you may not modify:\n{plan['test_code']}"
    if error:
        prompt += f"\n\nYour previous attempt failed:\n{error}"
    return unfence(call(BUILDER, "You are a staff engineer. Reply with one Python file, no prose.", prompt))


def pytest_run(paths, timeout, extra=()):
    return subprocess.run(
        [sys.executable, "-m", "pytest", *paths, "-q", "-p", "no:cacheprovider", *extra],
        capture_output=True, text=True, timeout=timeout, env=runner_env(),
    )


def failing_tests(paths=(), timeout=SUITE_TIMEOUT):
    """Ids of currently failing tests. Baseline-diffed, so pre-existing debt never blocks a build.

    Exit codes are part of the answer: 0 pass, 1 tests failed, 5 nothing collected. Anything else
    (2 interrupted/collection error, 3 internal, 4 usage) means the suite did not actually run --
    counting zero FAILED lines from that is how a broken suite reads as a green gate.
    """
    r = pytest_run(paths, timeout, ("--tb=no", "-rfE"))
    if r.returncode not in (0, 1, 5):
        raise SystemExit(f"[x] pytest exited {r.returncode} (suite did not run):\n"
                         f"{(r.stdout + r.stderr)[-ERR_CHARS:]}")
    ids = {l.split()[1] for l in r.stdout.splitlines()
           if l.startswith(("FAILED ", "ERROR ")) and len(l.split()) > 1}
    return ids, r


def gate(target, test_file, baseline):
    """AST parse -> scouted test -> whole suite vs baseline. Returns (passed, error_payload)."""
    try:
        with open(target) as source:
            ast.parse(source.read(), filename=target)
    except SyntaxError as e:
        return False, f"SyntaxError in {target}: {e}"
    try:
        r = pytest_run([test_file], GATE_TIMEOUT, ("--maxfail=1",))
        if r.returncode != 0:
            return False, (r.stdout + r.stderr)[-ERR_CHARS:]
        regressed, run = failing_tests()
    except subprocess.TimeoutExpired as e:
        return False, f"pytest timed out after {e.timeout}s; the change may not terminate"
    if regressed - baseline:
        return False, f"broke existing tests: {sorted(regressed - baseline)}\n{run.stdout[-ERR_CHARS:]}"
    return True, ""


def snapshot(*paths):
    snap = {}
    for p in paths:
        if os.path.exists(p):
            with open(p, "rb") as source:
                snap[p] = source.read()
        else:
            snap[p] = None
    return snap


def restore(snap):
    for p, data in snap.items():
        if data is None:
            if os.path.exists(p):
                os.remove(p)
        else:
            with open(p, "wb") as destination:
                destination.write(data)


def write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as destination:
        destination.write(text)


def review(goal, plan, target, before, after, evidence):
    """The reviewer reads the actual diff and the gate receipt, not just the finished file."""
    rel = os.path.relpath(target)
    diff = "".join(difflib.unified_diff(
        (before or "").splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=("/dev/null" if before is None else f"a/{rel}"), tofile=f"b/{rel}",
    ))[:DIFF_CHARS] or "(no textual change)"
    return call(
        REVIEWER,
        "You are a senior reviewer. Reply with concise markdown: release notes, then any risk the "
        "diff carries that the tests do not cover. Do not claim checks that are not listed.",
        f"Goal: {goal}\nPlan: {plan['summary']}\n\nVerification performed:\n{evidence}\n\n"
        f"Test contract ({os.path.relpath(plan['test_file'])}):\n{plan['test_code'][:DIFF_CHARS]}\n\n"
        f"Diff:\n{diff}",
        max_tokens=2048,
    )


def run(goal):
    print(f"[*] scout: {goal}")
    plan, target, test_file = scout(goal)
    print(f"[+] {os.path.relpath(target)}  (tests: {os.path.relpath(test_file)})")

    baseline, _ = failing_tests()
    if baseline:
        print(f"[~] {len(baseline)} test(s) already failing; ignoring those")
    snap = snapshot(target, test_file)  # every file we may touch, restored together on any failure
    current = snap[target].decode() if snap[target] is not None else None

    # One try around the whole lifecycle: provider error, timeout, Ctrl-C and gate exhaustion all
    # land on the same restore. Crash-during-write is still out of reach without a journal.
    try:
        contract = unfence(plan["test_code"])
        write(test_file, contract)

        error, attempts = "", 0
        for attempt in range(1, MAX_RETRIES + 1):
            attempts = attempt
            print(f"[*] build attempt {attempt}/{MAX_RETRIES}")
            code = build(goal, plan, current, error)  # generate first: an open() here truncates
            write(target, code)                       # the target while the request is still out
            passed, error = gate(target, test_file, baseline)
            with open(test_file) as source:
                if source.read() != contract:
                    raise SystemExit("[x] the test contract changed during the run; reverted")
            if passed:
                break
            print(f"[!] gate failed:\n{textwrap.indent(error[-600:], '    ')}")
        else:
            raise SystemExit(f"[x] gate never passed after {MAX_RETRIES} attempts; reverted")

        evidence = (f"- ast.parse on {os.path.relpath(target)}: ok\n"
                    f"- pytest {os.path.relpath(test_file)}: passed\n"
                    f"- full suite: no new failures vs a {len(baseline)}-failure baseline\n"
                    f"- build attempts: {attempts}/{MAX_RETRIES}\n"
                    f"- not checked: lint, types, runtime behaviour outside the contract")
        print(f"[+] gate passed\n{textwrap.indent(evidence, '    ')}\n[*] review")
        with open(target) as source:
            after = source.read()
        print(review(goal, plan, target, current, after, evidence))
        print(f"\n[=] left in the working tree, uncommitted: {os.path.relpath(target)}, "
              f"{os.path.relpath(test_file)}")
    except BaseException:
        restore(snap)
        raise


# --- checks -------------------------------------------------------------------------------------

def expect_exit(fn, because):
    try:
        fn()
    except SystemExit:
        return
    raise AssertionError(f"no SystemExit: {because}")


def fake_call(plan, build_result="def f(): return 2\n"):
    """Stand in for call(). build_result may be an exception instance to raise instead."""
    def _call(model, system, user, **kw):
        if model == SCOUT:
            return json.dumps(plan)
        if model == BUILDER:
            if isinstance(build_result, BaseException):
                raise build_result
            return build_result
        return "notes"
    return _call


def selftest_body():
    os.environ["ST_KEEP"] = "real"
    open("_st.env", "w").write('# comment\nexport ST_A="x y"\nST_B = 2 \n\nJUNK\nST_KEEP=clobbered\n')
    load_env("_st.env")
    assert (os.environ["ST_A"], os.environ["ST_B"], os.environ["ST_KEEP"]) == ("x y", "2", "real")
    load_env("_no_such.env")  # missing file is a no-op
    os.remove("_st.env")

    # F09: .env settings reach the runner; credentials do not
    os.environ["ST_OPENROUTER_API_KEY"] = "sk-leak"
    env = runner_env()
    assert env["ST_A"] == "x y" and env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "ST_OPENROUTER_API_KEY" not in env, "credential reached the runner"

    assert unfence("```python\nx = 1\n```") == "x = 1\n"
    assert unfence("x = 1") == "x = 1\n"
    assert unfence("```\na\nb\n```") == "a\nb\n"

    assert content_of({"choices": [{"message": {"content": "hi"}}]}) == "hi"
    for bad in [{}, {"choices": []}, {"choices": [{"message": {"content": None}}]},
                {"choices": [{"message": {"content": "  "}}]}, {"choices": [{"message": {}}]}, []]:
        assert content_of(bad) == "", f"accepted {bad!r}"
    assert finish_reason({"choices": [{"finish_reason": "length"}]}) == "length"

    assert parse_json('{"a": 1}') == {"a": 1}
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! Here is the plan:\n```json\n{"a": 1}\n```\nHope that helps.') == {"a": 1}
    assert parse_json('{"a": "}"}') == {"a": "}"}
    expect_exit(lambda: parse_json("no json here"), "non-JSON")

    # F06: a parseable envelope is not a valid plan
    ok = {"file": "pkg/mod.py", "summary": "s", "test_file": "tests/test_mod.py", "test_code": "x"}
    assert validate_plan(dict(ok)) is not None
    for bad in [[], None, 42, "str", {**ok, "summary": 7}, {**ok, "test_code": 1},
                {**ok, "file": ""}, {**ok, "file": "a" * 400}]:
        expect_exit(lambda b=bad: validate_plan(b), f"plan {bad!r}")

    # F03: containment, protected files, test-root policy
    assert safe_path("pkg/mod.py", "file") == os.path.join(ROOT, "pkg/mod.py")
    for bad in ["../evil.py", "/etc/evil.py", "a/../../evil.py", "", "notpy.txt", None,
                "aop.py", "conftest.py", "pkg/conftest.py", ".git/test_hidden.py"]:
        expect_exit(lambda b=bad: safe_path(b, "file"), f"path {bad!r}")
    for bad in ["tests/helpers.py", "test_root.py", "other/test_x.py"]:
        expect_exit(lambda b=bad: safe_path(b, "test_file"), f"test path {bad!r}")
    assert safe_path("tests/test_x.py", "test_file").endswith("tests/test_x.py")

    # F02: a suite that cannot run is not a green gate
    os.makedirs("tests", exist_ok=True)
    open("tests/test_broken.py", "w").write("import _no_such_module_at_all\n")
    expect_exit(failing_tests, "pytest exit 2 (collection error)")
    open("tests/test_broken.py", "w").write(
        "import pytest\n@pytest.fixture\ndef f(): raise RuntimeError('setup')\n"
        "def test_a(f): assert True\n")
    ids, _ = failing_tests()
    assert ids, "fixture ERROR line was not counted as a failure"
    os.remove("tests/test_broken.py")

    open("_st.py", "w").write("def f(:\n")
    assert gate("_st.py", "_st.py", set())[0] is False and "SyntaxError" in gate("_st.py", "_st.py", set())[1]
    open("_st.py", "w").write("def f(): return 2\n")
    open("test__st.py", "w").write("from _st import f\ndef test_f(): assert f() == 3\n")
    assert gate("_st.py", "test__st.py", set())[0] is False   # real failure, not a crash
    open("test__st.py", "w").write("from _st import f\ndef test_f(): assert f() == 2\n")
    assert gate("_st.py", "test__st.py", set())[0] is True

    snap = snapshot("_st.py", "_st_new.py")                    # one existing, one absent
    open("_st.py", "w").write("clobbered\n")
    open("_st_new.py", "w").write("created\n")
    restore(snap)
    assert open("_st.py").read() == "def f(): return 2\n" and not os.path.exists("_st_new.py")
    os.remove("_st.py"); os.remove("test__st.py")

    # F01 + F05: a provider failure mid-build must not truncate or orphan anything
    real_call = globals()["call"]
    plan = {"file": "pkg/mod.py", "summary": "add f", "test_file": "tests/test_mod.py",
            "test_code": "import sys; sys.path.insert(0, '.')\n"
                         "from pkg.mod import f\ndef test_f(): assert f() == 2\n"}
    try:
        write("pkg/mod.py", "ORIGINAL\n")
        globals()["call"] = fake_call(plan, SystemExit("[x] provider exploded"))
        expect_exit(lambda: run("add f"), "provider failure during build")
        assert open("pkg/mod.py").read() == "ORIGINAL\n", "target was truncated or overwritten"
        assert not os.path.exists("tests/test_mod.py"), "generated test survived the failure"

        # F03 again, end to end: same path for impl and contract never reaches a write
        globals()["call"] = fake_call({**plan, "test_file": "pkg/mod.py"})
        expect_exit(lambda: run("collide"), "file == test_file")
        assert open("pkg/mod.py").read() == "ORIGINAL\n"

        # happy path: gate passes, original preserved in the diff, files left in place
        globals()["call"] = fake_call(plan, "def f(): return 2\n")
        run("add f")
        assert open("pkg/mod.py").read() == "def f(): return 2\n"
        assert os.path.exists("tests/test_mod.py")
    finally:
        globals()["call"] = real_call

    # F10: help and usage errors stay local
    expect_exit(lambda: main(["--help"]), "--help")
    expect_exit(lambda: main([]), "no goal")
    expect_exit(lambda: main(["--nope"]), "unknown flag")


def selftest():
    """Runs in its own temp workspace: no fixed filenames in your tree, no discovery of your tests."""
    global ROOT
    if not __debug__:
        raise SystemExit("[x] selftest is assert-based; do not run it under -O")
    home, saved = os.getcwd(), ROOT
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.chdir(tmp)
            ROOT = os.path.realpath(tmp)
            subprocess.run(["git", "init", "-q"], check=True)
            selftest_body()
        finally:
            os.chdir(home)
            ROOT = saved
    print("ok")


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="aop.py", description=__doc__,
        epilog='example: aop.py "add a retry decorator with exponential backoff"')
    p.add_argument("goal", nargs="*", help="the feature to build, as one sentence")
    p.add_argument("--selftest", action="store_true", help="run the built-in checks and exit")
    p.add_argument("--version", action="version", version=f"aop {VERSION}")
    args = p.parse_args(argv)
    load_env()  # explicit, at entry: importing this module must not mutate the environment
    if args.selftest:
        return selftest()
    if not args.goal:
        p.error("give a goal, or --selftest")
    run(" ".join(args.goal))


if __name__ == "__main__":
    main()
