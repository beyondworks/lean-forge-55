# `python3 test_shell.py` — lean-forge-55 additions: shell-write gate (Claude Code bashFirst sessions, or not yet known), tree diff into the
# Castra ledger, per-turn undo, and background-task notices leaving the gate alone. Offline: Jev is switched off here.
import glob, hashlib, json, os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
H = os.path.join(HERE, "forge.py")
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
S = os.path.expanduser("~/.cache/lean-forge-55")
denied = lambda out: '"deny"' in out

# 1. what counts as a shell write (gate heuristic)
ns = {"__file__": H}; exec(open(H).read().split("def current_model")[0], ns)
w = ns["writes_files"]
for cmd in ("cat > invoicer/export.py <<'EOF'\nx\nEOF", "echo hi >> notes.md", "sed -i '' 's/a/b/' a.py", "tee out/x.csv",
            "python3 - <<'EOF'\nopen('invoicer/calc.py','w').write(s)\nEOF", "git commit -qm x", "rm invoicer/old.py"):
    assert w(cmd), f"write: {cmd!r}"
for cmd in ("cat invoicer/*.py", "python3 -m unittest discover -s tests 2>&1 | tail -3", "grep -rn total . > /dev/null",
            "python3 -c 'print(3 > 2, 0.1 > 0.05)'", "ls > /tmp/list.txt", "def f() -> int: pass", "git status && git diff"):
    assert not w(cmd), f"not a write: {cmd!r}"

# a repo and a transcript naming the model
REPO = tempfile.mkdtemp(prefix="lf55-")
subprocess.run("git init -q && mkdir app && printf 'A = 1\\n' > app/calc.py && git add -A && "
               "git -c user.email=t@t -c user.name=t commit -qm init", shell=True, cwd=REPO, check=True)
def transcript(model, bash_first):
    t = tempfile.mktemp(suffix=".jsonl")
    lines = [] if bash_first is None else [{"type": "attachment", "attachment": {"type": "auto_mode", "bashFirst": bash_first}}]
    lines.append({"type": "assistant", "message": {"model": model, "content": [{"type": "text", "text": "ok"}]}})
    open(t, "w").write("".join(json.dumps(x) + "\n" for x in lines))
    return t
def session(tag, model, bash_first=True):
    sid = f"selftest55-{tag}-{os.getpid()}"
    tp = transcript(model, bash_first) if model else ""
    def call(ev, **kw):
        env = dict(os.environ, LEAN_FORGE_JEV="off")
        return subprocess.run([sys.executable, H, ev], input=json.dumps({"session_id": sid, "cwd": REPO, "transcript_path": tp, **kw}),
                              capture_output=True, text=True, env=env).stdout
    return call, sid
bash = lambda c: {"tool_name": "Bash", "tool_input": {"command": c}}
WRITE = "cat > app/new.py <<'EOF'\nB = 2\nEOF"

# 2. bashFirst session: closed gate denies shell writes, not reads, for any model; without bashFirst, plain lean-forge
call, sid = session("o55", "claude-opus-5-5")
call("prompt", prompt="새 모듈 만들어 줘")
assert denied(call("pre", tool_use_id="t1", **bash(WRITE))), "5.5: shell write denied while closed"
assert not denied(call("pre", tool_use_id="t2", **bash("cat app/calc.py"))), "5.5: reading stays open"
call("post", tool_use_id="t2", **bash("cat app/calc.py"))
call2, _ = session("o5", "claude-opus-5")
call2("prompt", prompt="새 모듈 만들어 줘")
assert denied(call2("pre", tool_use_id="u1", **bash(WRITE))), "bashFirst + another model: shell write denied too"
call4, _ = session("nobf", "claude-opus-5-5", bash_first=None)
call4("prompt", prompt="새 모듈 만들어 줘")
assert not denied(call4("pre", tool_use_id="w1", **bash(WRITE))), "no bashFirst steering recorded: Bash untouched (plain lean-forge)"
call3, _ = session("unknown", None)
call3("prompt", prompt="새 모듈 만들어 줘")
assert denied(call3("pre", tool_use_id="v1", **bash(WRITE))), "transcript not written yet: shell rules apply"

# 3. a background-task notice does not re-triage the gate
call("stop"); st = json.load(open(f"{S}/{sid}.json")); assert st["state"] == "asked"
call("prompt", prompt="<task-notification><task-id>x</task-id><status>completed</status></task-notification>")
assert json.load(open(f"{S}/{sid}.json"))["state"] == "asked", "notification leaves the state as it was"
call("prompt", prompt="네, 그렇게 해 주세요")  # the user's answer (Jev off: unknown counts as an answer)
assert json.load(open(f"{S}/{sid}.json"))["state"] == "open"

# 4. open: the write runs, the diff records it in the Castra ledger, undo restores the tree
out = call("pre", tool_use_id="t3", **bash(WRITE)); assert not denied(out)
subprocess.run(WRITE + "\nprintf 'A = 9\\n' > app/calc.py", shell=True, cwd=REPO, check=True)
out = call("post", tool_use_id="t3", **bash(WRITE))
assert "app/new.py" in out and "app/calc.py" in out, out
ledger = os.path.expanduser("~/.castra/sessions/" + hashlib.sha256(sid.encode()).hexdigest() + ".json")
files = json.load(open(ledger))["files"]
assert any(k.endswith("app/new.py") and v["status"] == "pending" for k, v in files.items()), "shell-written file is pending in Castra"
r = subprocess.run([sys.executable, os.path.join(HERE, "..", "scripts", "lf55_snapshot.py"), "undo", "--session", sid],
                   capture_output=True, text=True)
res = json.loads(r.stdout)
assert open(f"{REPO}/app/calc.py").read() == "A = 1\n", "undo restored the edited file"
assert not os.path.exists(f"{REPO}/app/new.py") and os.path.exists(os.path.join(res["aside_folder"], "before-undo", "app/new.py")), \
    "created file moved aside, not lost"

for f in glob.glob(f"{S}/selftest55-*-{os.getpid()}.*") + [ledger]:
    os.remove(f)
for d in glob.glob(os.path.join(S, "snapshots", f"selftest55-*-{os.getpid()}")):
    import shutil; shutil.rmtree(d)
print("shell ok (write heuristic, bashFirst-keyed gate, notification, Castra ledger, undo)")
