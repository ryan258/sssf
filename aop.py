#!/usr/bin/env python3
"""Autonomous OpenRouter Pipeline: scout -> build -> gate -> review, one file at a time."""
import ast, json, os, re, subprocess, sys, textwrap, urllib.error, urllib.request

# All :free tier, all advertise structured_outputs. Free tiers are rate-limited per day, so a
# long retry loop can 429 -- call() surfaces the body so you'll know. Re-check with:
#   curl -s https://openrouter.ai/api/v1/models | jq '.data[] | select(.pricing.prompt=="0")'
SCOUT = "nvidia/nemotron-3-super-120b-a12b:free"   # biggest of the free set; writes plan + tests
BUILDER = "openai/gpt-oss-20b:free"
REVIEWER = "google/gemma-4-26b-a4b-it:free"
MAX_RETRIES = 3
ERR_CHARS = 2000

API = "https://openrouter.ai/api/v1/chat/completions"
FENCE = re.compile(r"^\s*```[a-zA-Z]*\n(.*?)\n```\s*$", re.S)
ROOT = os.path.realpath(".")
NO_PYC = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def load_env(path=".env"):
    """ponytail: 6 lines beats a python-dotenv dep. Real environment wins, same as dotenv."""
    for line in open(path) if os.path.exists(path) else []:
        line = line.strip().removeprefix("export ")
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


load_env()


def call(model, system, user, json_mode=False, max_tokens=8192):
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is not set")
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
    # ponytail: free models are load-balanced across providers and some return null content.
    # One retry re-rolls the provider; more than that is a backoff library nobody asked for.
    err = ""
    for _ in range(2):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:  # auth/quota/model: deterministic, body says why
            raise SystemExit(f"[x] OpenRouter {e.code}: {e.read().decode(errors='replace')[:500]}")
        except (urllib.error.URLError, TimeoutError) as e:  # reset mid-handshake: just re-dial
            err = str(e)
            print(f"    [{model}] {err}; retrying")
            continue
        u = data.get("usage") or {}
        print(f"    [{model}] {u.get('prompt_tokens', '?')}+{u.get('completion_tokens', '?')} tok")
        text = content_of(data)
        if text:
            return text
        # OpenRouter can return 200 with an error body; without this it reads as "empty content"
        err = (json.dumps(data["error"])[:300] if data.get("error")
               else f"empty content from {data.get('provider')!r}")
        print(f"    [{model}] {err}; re-rolling provider")
    raise SystemExit(f"[x] {model} failed twice ({err}); rerun, or pick another model")


def content_of(data):
    """Free-tier providers intermittently return null content. '' means 'nothing usable'."""
    choice = (data.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content")
    return text if isinstance(text, str) and text.strip() else ""


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
    if kind == "test_file" and not os.path.basename(full).startswith("test_"):
        raise SystemExit(f"[x] scout test_file is not collectable by pytest: {p!r}")
    return full


def scout(goal):
    tree = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout
    raw = call(
        SCOUT,
        "You are a lead engineer. Reply with JSON only, keys: file (one relative path to create or "
        "modify), summary, test_file (NEW relative path under tests/, basename starting test_), "
        "test_code (complete pytest module that imports `file` and fully specifies the feature). "
        "Tests are the contract: they must pass only if the feature is correct.",
        f"Goal: {goal}\n\nRepo files:\n{tree[:4000]}",
        json_mode=True,  # honoured when the scout supports it, dropped otherwise; parse_json covers both
        max_tokens=8192,  # plan + a complete test module; 4096 truncated mid-JSON
    )
    plan = parse_json(raw)
    missing = [k for k in ("file", "summary", "test_file", "test_code") if not plan.get(k)]
    if missing:
        raise SystemExit(f"[x] scout plan is missing {missing}:\n{raw[:500]}")
    return plan, safe_path(plan["file"], "file"), safe_path(plan["test_file"], "test_file")


def build(goal, plan, current, error):
    prompt = f"Goal: {goal}\nPlan: {plan['summary']}\nWrite the complete contents of {plan['file']}."
    if current is not None:
        prompt += f"\n\nCurrent contents of {plan['file']}:\n{current}"
    prompt += f"\n\nIt must pass these tests, which you may not modify:\n{plan['test_code']}"
    if error:
        prompt += f"\n\nYour previous attempt failed:\n{error}"
    return unfence(call(BUILDER, "You are a staff engineer. Reply with one Python file, no prose.", prompt))


def failing_tests(paths=()):
    """Ids of currently failing tests. Baseline-diffed, so pre-existing debt never blocks a build."""
    r = subprocess.run(
        [sys.executable, "-m", "pytest", *paths, "-q", "--tb=no", "-rf", "-p", "no:cacheprovider"],
        capture_output=True, text=True, timeout=600, env=NO_PYC,
    )
    return {l.split()[1] for l in r.stdout.splitlines() if l.startswith("FAILED ")}, r


def gate(target, test_file, baseline):
    """AST parse -> scouted test -> whole suite vs baseline. Returns (passed, error_payload)."""
    try:
        ast.parse(open(target).read(), filename=target)
    except SyntaxError as e:
        return False, f"SyntaxError in {target}: {e}"
    # DONTWRITEBYTECODE: rewrites of the same size within one second reuse a stale .pyc
    r = subprocess.run(
        [sys.executable, "-m", "pytest", test_file, "-q", "--maxfail=1", "-p", "no:cacheprovider"],
        capture_output=True, text=True, timeout=120, env=NO_PYC,
    )
    if r.returncode != 0:
        return False, (r.stdout + r.stderr)[-ERR_CHARS:]
    regressed, run = failing_tests()
    if regressed - baseline:
        return False, f"broke existing tests: {sorted(regressed - baseline)}\n{run.stdout[-ERR_CHARS:]}"
    return True, ""


def snapshot(*paths):
    return {p: (open(p, "rb").read() if os.path.exists(p) else None) for p in paths}


def restore(snap):
    for p, data in snap.items():
        if data is None:
            if os.path.exists(p):
                os.remove(p)
        else:
            open(p, "wb").write(data)


def run(goal):
    print(f"[*] scout: {goal}")
    plan, target, test_file = scout(goal)
    print(f"[+] {os.path.relpath(target)}  (tests: {os.path.relpath(test_file)})")

    baseline, _ = failing_tests()
    if baseline:
        print(f"[~] {len(baseline)} test(s) already failing; ignoring those")
    snap = snapshot(target, test_file)  # every file we may touch, restored together on failure
    current = snap[target].decode() if snap[target] is not None else None

    os.makedirs(os.path.dirname(test_file), exist_ok=True)
    open(test_file, "w").write(unfence(plan["test_code"]))

    error = ""
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"[*] build attempt {attempt}/{MAX_RETRIES}")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        open(target, "w").write(build(goal, plan, current, error))
        passed, error = gate(target, test_file, baseline)
        if passed:
            break
        print(f"[!] gate failed:\n{textwrap.indent(error[-600:], '    ')}")
    else:
        restore(snap)
        raise SystemExit(f"[x] gate never passed after {MAX_RETRIES} attempts; reverted")

    print("[+] gate passed\n[*] review")
    print(call(REVIEWER, "You are a senior reviewer. Reply with concise markdown release notes.",
               f"Goal: {goal}\n\n{os.path.relpath(target)}:\n{open(target).read()}", max_tokens=2048))


def selftest():
    os.environ["ST_KEEP"] = "real"
    open("_st.env", "w").write('# comment\nexport ST_A="x y"\nST_B = 2 \n\nJUNK\nST_KEEP=clobbered\n')
    load_env("_st.env")
    assert (os.environ["ST_A"], os.environ["ST_B"], os.environ["ST_KEEP"]) == ("x y", "2", "real")
    load_env("_no_such.env")  # missing file is a no-op
    os.remove("_st.env")

    assert unfence("```python\nx = 1\n```") == "x = 1\n"
    assert unfence("x = 1") == "x = 1\n"
    assert unfence("```\na\nb\n```") == "a\nb\n"

    assert content_of({"choices": [{"message": {"content": "hi"}}]}) == "hi"
    for bad in [{}, {"choices": []}, {"choices": [{"message": {"content": None}}]},
                {"choices": [{"message": {"content": "  "}}]}, {"choices": [{"message": {}}]}]:
        assert content_of(bad) == "", f"accepted {bad!r}"

    assert parse_json('{"a": 1}') == {"a": 1}
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! Here is the plan:\n```json\n{"a": 1}\n```\nHope that helps.') == {"a": 1}
    assert parse_json('{"a": "}"}') == {"a": "}"}
    try:
        parse_json("no json here")
        raise AssertionError("accepted non-JSON")
    except SystemExit:
        pass

    assert safe_path("pkg/mod.py", "file") == os.path.join(ROOT, "pkg/mod.py")
    for bad in ["../evil.py", "/etc/evil.py", "a/../../evil.py", "", "notpy.txt", None]:
        try:
            safe_path(bad, "file")
            raise AssertionError(f"accepted {bad!r}")
        except SystemExit:
            pass
    try:
        safe_path("tests/helpers.py", "test_file")
        raise AssertionError("accepted non-test_ filename")
    except SystemExit:
        pass

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
    print("ok")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--selftest"]:
        selftest()
    elif len(sys.argv) > 1:
        run(" ".join(sys.argv[1:]))
    else:
        raise SystemExit('usage: aop.py "add a light/dark theme switcher with persistence"')
