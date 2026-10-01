# `python3 test_castra_stop.py` — 0.2.5 Castra Stop scope, offline. Stop blocks only for code this turn's own agent
# changed and that still exists; the rest is reported without blocking (318 real Stop blocks, most on older or
# subagent files, 2 that found real defects).
import json, os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
HOME = tempfile.mkdtemp(prefix="lf55-castra-")
ENV = dict(os.environ, CASTRA_HOME=HOME, HOME=HOME)  # HOME too: ~/.claude/... paths resolve inside the temp folder
W = tempfile.mkdtemp(prefix="lf55-work-")
RUNTIME = os.path.join(HERE, "..", "scripts", "castra_runtime.py")


def hook(name, payload):
    return subprocess.run([sys.executable, os.path.join(HERE, name)], input=json.dumps(payload),
                          capture_output=True, text=True, env=ENV).stdout


def session(tag):
    sid = f"stop-{tag}"
    def edit(path, agent=None, body="x = 1\n"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").write(body)
        p = {"session_id": sid, "cwd": W, "hook_event_name": "PostToolUse", "tool_name": "Write",
             "tool_input": {"file_path": path}, "tool_response": {"success": True}}
        if agent:
            p["agent_id"] = agent
        hook("castra-trace.py", p)
    turn = lambda: hook("castra-route.py", {"session_id": sid, "cwd": W, "prompt": "next"})
    stop = lambda: hook("castra-openloop.py", {"session_id": sid, "cwd": W, "stop_hook_active": False})
    run = lambda *a, stdin=None: subprocess.run([sys.executable, RUNTIME, *a, "--session", sid] if a[0] == "status" else
                                                [sys.executable, RUNTIME, a[0], "--session", sid, *a[1:]],
                                                input=stdin, capture_output=True, text=True, env=ENV, cwd=W)
    return edit, turn, stop, run


# this turn's own edit blocks, also in the scratchpad (28957910 09-28 10:01: the check found a script that timed out)
edit, turn, stop, run = session("mine")
turn(); edit(f"{W}/scratch/analyze.py")
assert '"block"' in stop()

# an edit from an earlier turn is reported once, not blocked again (its own turn already had the chance)
edit, turn, stop, run = session("earlier")
turn(); edit(f"{W}/app/old.py"); stop(); stop()
turn()
out = stop()
assert '"block"' not in out and "earlier turns" in out, out
assert "earlier turns" not in stop(), "said once until the count grows"

# a subagent's edit does not block the parent (this audit's own session was blocked five times by subagent temp files)
edit, turn, stop, run = session("sub")
turn(); edit(f"{W}/tmp/sub.py", agent="a1234")
assert '"block"' not in stop()

# a file that no longer exists does not block
edit, turn, stop, run = session("gone")
turn(); edit(f"{W}/app/gone.py"); os.remove(f"{W}/app/gone.py")
assert '"block"' not in stop()

# notes and installed copies are not results; hooks the agent writes in ~/.claude are code and stay tracked
edit, turn, stop, run = session("paths")
turn()
edit(f"{HOME}/.claude/projects/-x/memory/helper.py"); edit(f"{HOME}/.claude/plugins/cache/p/1.0/hooks/h.py")
assert '"block"' not in stop()
edit(f"{HOME}/.claude/hooks/guard.py")
assert '"block"' in stop()

# deferred keeps its reason when something else changes the file (another session rewrote a shared MEMORY.md)
edit, turn, stop, run = session("defer")
turn(); edit(f"{W}/app/ext.py")
run("defer", "--file", f"{W}/app/ext.py", "--reason", "external-access", "--note", "needs the staging key")
open(f"{W}/app/ext.py", "w").write("x = 2\n")  # changed outside this session's edit tools
item = json.loads(run("status").stdout)["files"][os.path.realpath(f"{W}/app/ext.py")]
assert item["status"] == "deferred" and item["reason"] == "external-access" and item["note"] == "needs the staging key", item
edit(f"{W}/app/ext.py", body="x = 3\n")  # the agent's own new edit is a new change to check
assert json.loads(run("status").stdout)["files"][os.path.realpath(f"{W}/app/ext.py")]["status"] == "pending"

# the deferred notice is not repeated on every Stop
edit, turn, stop, run = session("defer-once")
turn(); edit(f"{W}/app/ext2.py"); run("defer", "--file", f"{W}/app/ext2.py", "--reason", "environment")
assert "deferred/blocked" in stop() and "deferred/blocked" not in stop()

# verify: --pending, --files-from -, and timeouts up to 1800 s (a 20-minute analysis was refused at 300 s)
edit, turn, stop, run = session("verify")
turn(); edit(f"{W}/app/a.py"); edit(f"{W}/app/b.py")
r = run("verify", "--pending", "--timeout", "1200", "--", sys.executable, "-c", "pass")
assert json.loads(r.stdout)["status"] == "verified" and json.loads(r.stdout)["files"] == 2, r.stdout + r.stderr
edit(f"{W}/app/c.py")
r = run("verify", "--files-from", "-", "--", sys.executable, "-c", "pass", stdin=f"{W}/app/c.py\n")
assert json.loads(r.stdout)["status"] == "verified", r.stdout + r.stderr
assert '"block"' not in stop()

# CLAUDE_CODE_SESSION_ID is enough to name the session
r = subprocess.run([sys.executable, RUNTIME, "status"], capture_output=True, text=True,
                   env=dict(ENV, CLAUDE_CODE_SESSION_ID="stop-verify"), cwd=W)
assert r.returncode == 0 and '"files"' in r.stdout, r.stderr
print("castra stop ok (this turn, earlier turns, subagents, removed, untracked paths, deferred, verify options, session env)")
