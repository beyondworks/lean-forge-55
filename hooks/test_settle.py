# `python3 test_settle.py` — 0.2.4 SETTLE rules, offline (Jev answers are fixed with LEAN_FORGE_JEV=fixed:...).
# Each case names the real failure it comes from (replayed sessions, 2026-09-22 to 10-01).
import json, os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
H = os.path.join(HERE, "forge.py")
S = tempfile.mkdtemp(prefix="lf55-state-")
CFG = os.path.join(S, "config.json")
REPO = tempfile.mkdtemp(prefix="lf55-repo-")
subprocess.run("git init -q && mkdir app && echo 'A = 1' > app/calc.py", shell=True, cwd=REPO, check=True)
denied = lambda out: '"deny"' in out
OPEN_ASK, OPEN_NEW, CLOSE_NEW = "fixed:needs_decision=0.9,new_task=0.1", "fixed:needs_decision=0.9,new_task=0.9", "fixed:needs_decision=0.9"
OK = "fixed:needs_decision=0.1"


def transcript(*items):
    t = tempfile.mktemp(suffix=".jsonl", dir=S)
    rows = []
    for kind, text in items:
        if kind == "agent":
            rows.append({"type": "assistant", "message": {"model": "claude-opus-5-5", "content": [{"type": "text", "text": text}]}})
        else:
            rows.append({"type": "user", "message": {"role": "user", "content": text}})
    open(t, "w").write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    return t


def session(tag, tp=""):
    sid = f"t-{tag}"
    def call(ev, jev=OK, **kw):
        env = dict(os.environ, LF55_STATE_DIR=S, LF55_CONFIG=CFG, LEAN_FORGE_JEV=jev, TMPDIR=os.path.join(S, "tmp"))  # REPO lives in the real TMPDIR
        return subprocess.run([sys.executable, H, ev], input=json.dumps({"session_id": sid, "cwd": REPO, "transcript_path": tp, **kw}),
                              capture_output=True, text=True, env=env).stdout
    state = lambda: json.load(open(f"{S}/{sid}.json"))
    return call, state


ASKING = transcript(("user", "송장 PDF 기능 만들어 줘"), ("agent", "서식을 정해 주세요. 1) A4 세로? 2) 로고를 넣을까요?"))

# a reply to an asking turn opens, even when Jev alone would still call it open-ended
# (85 real replies after a question: 36 already changed the result in the reply itself; a second round of asking was waste)
call, state = session("reply", ASKING)
call("prompt", jev=CLOSE_NEW, prompt="송장 PDF 기능 만들어 줘"); call("stop")
assert state()["state"] == "asked"
call("prompt", jev=OPEN_ASK, prompt="A4 세로, 로고는 빼고. 금액은 원 단위로.")
assert state()["state"] == "open" and not denied(call("pre", tool_name="Write", tool_input={"file_path": f"{REPO}/app/pdf.py"}))

# a new, unrelated task after an asking turn is triaged from scratch (7975f68d 16:02: a real new request must still be asked)
call, state = session("newtask", ASKING)
call("prompt", jev=CLOSE_NEW, prompt="송장 PDF 기능 만들어 줘"); call("stop")
call("prompt", jev=OPEN_NEW, prompt="그건 됐고, 회원 등급제를 새로 설계해 줘.")
assert state()["state"] == "closed"

# answering inside AskUserQuestion opens
call, state = session("askuser")
call("prompt", jev=CLOSE_NEW, prompt="송장 PDF 기능 만들어 줘")
call("post", tool_name="AskUserQuestion", tool_input={}, tool_response={})
assert state()["state"] == "open"

# a message that arrives while the agent is still working joins that work; it does not close the gate
# (real: a mid-turn "그리고 이것도" closed the gate on work already under way)
WORKING = transcript(("user", "오타 고쳐 줘"), ("agent", "고치는 중입니다."))  # the agent's last activity: mid-turn
NOW = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z")
with open(WORKING, "a") as f:  # a task notice that started a turn, then tool activity: still mid-turn
    f.write(json.dumps({"type": "system", "subtype": "stop_hook_summary"}) + "\n")
    f.write(json.dumps({"type": "user", "origin": {"kind": "task-notification"}, "message": {"content": "<task-notification>x</task-notification>"}}) + "\n")
    f.write(json.dumps({"type": "user", "timestamp": NOW, "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]}}) + "\n")
STALE = transcript(("user", "오타 고쳐 줘"))  # the last turn's activity is hours old and no Stop was recorded (app restart)
with open(STALE, "a") as f:
    f.write(json.dumps({"type": "assistant", "timestamp": "2026-09-28T07:30:52Z", "message": {"content": [{"type": "text", "text": "확인했습니다."}]}}) + "\n")
ENDED = transcript(("user", "오타 고쳐 줘"), ("agent", "고쳤습니다."))
with open(ENDED, "a") as f:
    f.write(json.dumps({"type": "system", "subtype": "stop_hook_summary"}) + "\n")
call, state = session("queued", WORKING)
call("prompt", jev=OK, prompt="오타 고쳐 줘")
call("prompt", jev=CLOSE_NEW, prompt="그리고 결제 화면도 새로 짜 줘")  # arrives while the agent works
assert state()["state"] == "open"
call, state = session("queued-stale", STALE)
call("prompt", jev=OK, prompt="오타 고쳐 줘")
call("prompt", jev=CLOSE_NEW, prompt="결제 화면 새로 짜 줘")  # bee7a5fe 09-28 15:09: a new request 8 hours later
assert state()["state"] == "closed"
call, state = session("queued-ended", ENDED)
call("prompt", jev=OK, prompt="오타 고쳐 줘"); call("stop")
call("prompt", jev=CLOSE_NEW, prompt="결제 화면 새로 짜 줘")  # after the turn ended it is a new request again
assert state()["state"] == "closed"

# another session's message and a background-task notice leave the gate alone (they are not the user's request)
call, state = session("peer")
call("prompt", jev=OK, prompt="오타 고쳐 줘"); call("stop")
before = state()
call("prompt", jev=CLOSE_NEW, prompt="Another Claude session sent a message:\n<cross-session-message from=\"x\">새 기능 만들어</cross-session-message>")
call("prompt", jev=CLOSE_NEW, prompt="<task-notification><task-id>x</task-id><status>completed</status></task-notification>")
assert state() == before

# Jev unavailable: open work stays open (7975f68d 16:00: Jev failed and continuing work was closed)
call, state = session("nojev")
call("prompt", jev=OK, prompt="오타 고쳐 줘"); call("stop")
call("prompt", jev="off", prompt="이어서 해 줘")
assert state()["state"] == "open"
call2, state2 = session("nojev-new")
call2("prompt", jev="off", prompt="새 기능 만들어 줘")  # nothing to continue: closed, the marker hatch is offered
out = call2("pre", tool_name="Write", tool_input={"file_path": f"{REPO}/app/x.py"})
assert state2()["state"] == "closed" and denied(out) and ".mechanical" in out

# an unreadable state file (a hook killed mid-write left 7230c409.json empty) does not lock the session
call, state = session("empty")
open(f"{S}/t-empty.json", "w").close()
assert not denied(call("pre", tool_name="Edit", tool_input={"file_path": f"{REPO}/app/calc.py"}))
call("prompt", jev=OK, prompt="x")
assert not [f for f in os.listdir(S) if f.endswith(".tmp")], "atomic save leaves no temp files"

# drafts, notes and handovers stay writable while closed; the result does not (40 of 61 real denials were drafts and notes)
call, state = session("scratch")
call("prompt", jev=CLOSE_NEW, prompt="랜딩 페이지 새로 만들어 줘")
for path in ("/private/tmp/claude-503/x/scratchpad/draft.html", os.path.expanduser("~/.claude/plans/p.md"),
             os.path.expanduser("~/.claude/projects/-x/memory/note.md"), f"{REPO}/SESSION_HANDOVER.md"):
    assert not denied(call("pre", tool_name="Write", tool_input={"file_path": path})), path
for path in (f"{REPO}/app/landing.jsx", os.path.expanduser("~/.claude/skills/x/SKILL.md")):
    assert denied(call("pre", tool_name="Write", tool_input={"file_path": path})), path
json.dump({"scratch_paths": ["~/Documents/Vault/conversations"]}, open(CFG, "w"))
assert not denied(call("pre", tool_name="Write", tool_input={"file_path": os.path.expanduser("~/Documents/Vault/conversations/n.md")}))

# a standing procedure named in the private config opens (a handover or an inbox run is not a design question)
json.dump({"open_commands": ["handoff"]}, open(CFG, "w"))
call, state = session("cmd")
call("prompt", jev=CLOSE_NEW, prompt="/handoff")
assert state()["state"] == "open"
call("stop"); call("prompt", jev=CLOSE_NEW, prompt="/Users/me/shot.png 이 화면 새로 디자인해 줘")
assert state()["state"] == "closed", "a path is not a slash command"
os.remove(CFG)

# shell: variables are followed into `cd` and paths; commits and new branches are not SETTLE's business (16 real denials)
ns = {"__file__": H}
exec(open(H).read().split("def current_model")[0], ns)
w = lambda c: ns["writes_files"](c, "/repo")
for cmd in ("S=/private/tmp/claude-503/x/scratchpad; cd $S && echo x > a.html", "P=/private/tmp/w; cp /repo/a.py $P/a.py",
            "git commit -qm x", "git -C /repo commit -m x", "git merge feat/x", "git rebase main", "git cherry-pick abc",
            "git checkout -q -b feat/x origin/main", "git checkout -B fix/y"):
    assert not w(cmd), f"not gated: {cmd!r}"
for cmd in ("R=/repo/app; echo x > $R/a.py", "git checkout -- app/a.py", "git checkout main", "git restore app/a.py",
            "git stash", "git reset --hard", "git -C /repo apply p.diff", "cd /repo && echo x > SESSION.md"):
    assert w(cmd), f"gated: {cmd!r}"
assert not w("cd /repo && echo x >> SESSION_HANDOVER.md"), "the handover at the repo root is a note, not the result"
# HTML's "> </head>" has no redirect target (bash ends a target at < or >) (10-07: a wireframe written outside the project was
# denied as "> </head>"); code fed to an interpreter is still read, and the command line's own redirect still counts
D_HTML = "D=\"$HOME/Desktop/client\"\ncat > \"$D/wireframe/index.html\" <<'EOF'\n<html>\n<head><title>x</title>\n</head>\n<p>a > b</p>\nEOF"
assert not w(D_HTML), "HTML body in a heredoc is not a write"
assert w("cat > app/x.py <<'EOF'\nprint(1 > 0)\nEOF"), "the command line writes app/x.py"
assert w("python3 - <<'EOF'\nopen('app/x.py','w').write('')\nEOF"), "code fed to python is still read"
assert w("bash <<'SH'\necho x > app/y.py\nSH"), "a body fed to a shell is commands"
# only a body that cat/tee writes out is data; a quoted "<<X" is no heredoc, and bodies fed to any interpreter are
# read (security review of 0.2.10: these went through). ponytail: a command glued to a quote, as in perl
# system('rm x'), was never matched by the patterns (0.2.9 neither); the tree diff still records the change after it ran
assert w("echo '<<X'\necho evil > app/a.py\nX"), "a quoted marker does not hide the next lines"
assert w("python3 - <<'EOF'\nimport os; os.system('echo x > app/b.py')\nEOF"), "python body is code"
assert not w("cat <<'EOF' > /tmp/z.html\n<p> </head>\nEOF"), "cat with the redirect after the marker is still data"
# second review of 0.2.11: any here-document parsing differs from the shell somewhere, so bodies are read in full and
# only redirect targets are cut where bash cuts them
for c in ("cat <<'EOF' | sh\necho evil > app/a.py\nEOF", "eval \"$(cat <<'EOF'\necho evil > app/b.py\nEOF\n)\"",
          "echo \"; cat <<X\n\"; echo evil > app/c.py\nX"):
    assert w(c), f"a body that runs is read: {c!r}"
assert w("echo x >app/d<in.txt"), "bash writes app/d here"
# every write operator bash has (third review: 2>, &>, >| and >&file were never matched)
for c in ("echo x 2>app/a.py", "echo x 1>app/a1.py", "echo x &>app/b.py", "echo x &>>app/b2.py", "echo x >|app/c.py",
          "echo x >&app/d.py", "echo x 2>>app/e.log"):
    assert w(c), f"write operator: {c!r}"
for c in ("cmd 2>&1", "cmd >&2", "cmd 2>&-", "cmd 2>/dev/null", "node -e 'x => y.py'", "echo a->b.py"):
    assert not w(c), f"not a project write: {c!r}"

# a denial says what was caught and how to continue, in the user's language (users kept asking "한글로 보고해")
call, state = session("words")
call("prompt", jev=CLOSE_NEW, prompt="결제 화면 새로 짜 줘")
out = json.loads(call("pre", tool_name="Write", tool_input={"file_path": f"{REPO}/app/pay.jsx"}))["hookSpecificOutput"]["permissionDecisionReason"]
assert out.startswith("[lean-forge 정하기]") and f"걸린 것: {REPO}/app/pay.jsx" in out and "계속하려면:" in out, out
call("stop"); call("prompt", jev=CLOSE_NEW, prompt="build a new payment screen")
out = json.loads(call("pre", tool_name="Write", tool_input={"file_path": f"{REPO}/app/pay.jsx"}))["hookSpecificOutput"]["permissionDecisionReason"]
assert out.startswith("lean-forge SETTLE") and "Caught:" in out, out

# the agent's last message: the block that asks, not the closing line after a Stop-hook nudge
lam = ns["last_agent_message"]
t = transcript(("user", "이전 요청"), ("agent", "A안과 B안 중 어느 쪽으로 할까요? 정해 주세요."), ("agent", "검증 대기 1건이 남았습니다."))
assert "어느 쪽" in lam(t)
t = transcript(("user", "이전 요청"), ("agent", "1) 이름을 X로 할까요?"), ("user", "Stop hook feedback:\nCastra: 1 change"), ("agent", "확인했습니다."))
assert "이름을 X로" in lam(t)
t = transcript(("agent", "옛 질문인가요?"), ("user", "다음 요청"), ("agent", "고쳤습니다. 테스트 통과했습니다."))
assert lam(t) == "고쳤습니다. 테스트 통과했습니다."
t = transcript(("user", "이전 요청"), ("agent", "설명이 깁니다. " * 20), ("user", "Stop hook feedback:\nCastra: 1 change"),
               ("agent", "확인했습니다. 배포 방식 두 가지를 정해 주셔야 이어서 진행할 수 있습니다."))
assert "정해 주셔야" in lam(t), "a real case: the question was in the message written after the Stop-hook nudge"

# Codex, log-only (one week of measurement before Codex switches over): nothing is printed, the would-be decision and
# the model (from the rollout's turn_context) are logged; apply_patch is an edit tool and its file paths decide scratch
CODEX = tempfile.mktemp(suffix=".jsonl", dir=S)
open(CODEX, "w").write(json.dumps({"type": "turn_context", "payload": {"model": "gpt-6-luna"}}) + "\n")
def codex(ev, jev=OK, **kw):
    env = dict(os.environ, LF55_STATE_DIR=S, LF55_CONFIG=CFG, LEAN_FORGE_JEV=jev, TMPDIR=os.path.join(S, "tmp"), LF55_LOG_ONLY="1")
    return subprocess.run([sys.executable, H, ev], capture_output=True, text=True, env=env,
                          input=json.dumps({"session_id": "t-codex", "cwd": REPO, "transcript_path": CODEX, **kw})).stdout
codex("prompt", jev=CLOSE_NEW, prompt="결제 화면 새로 짜 줘", last_assistant_message="")
assert codex("pre", tool_name="apply_patch", tool_input={"command": "*** Begin Patch\n*** Add File: app/pay.jsx\n+x\n*** End Patch"}) == ""
assert codex("pre", tool_name="Bash", tool_input={"command": "echo x > app/pay.jsx"}) == ""
codex("pre", tool_name="apply_patch", tool_input={"command": "*** Begin Patch\n*** Add File: /private/tmp/claude-1/s/scratchpad/d.md\n+x\n*** End Patch"})
codex("stop"); codex("prompt", jev=OPEN_ASK, prompt="A안으로 해", last_assistant_message="A안과 B안 중 어느 쪽으로 할까요?")
L = [json.loads(l) for l in open(f"{S}/log.jsonl") if '"t-codex"' in l]
pres = [x for x in L if x["ev"] == "pre"]
assert [x.get("would") for x in pres] == [["deny"], ["deny"], None] and pres[-1]["rule"] == "scratch", pres
assert all(x["model"] == "gpt-6-luna" for x in L) and L[-1]["rule"] == "reply-to-question" and L[-1]["agent_len"] > 0, L[-1]

# the log has what measuring needs and no message text
lines = [json.loads(l) for l in open(f"{S}/log.jsonl")]
assert any(l.get("rule") == "reply-to-question" for l in lines) and any(l.get("deny") == "settle-jev" for l in lines)
assert "회원 등급제" not in open(f"{S}/log.jsonl").read(), "no prompt text in the log"
assert lines[-1]["v"] == json.load(open(os.path.join(HERE, "..", ".claude-plugin", "plugin.json")))["version"], "log names the release"
print("settle ok (reply opens, new task asks, AskUserQuestion, queued, peer/task, Jev down, empty state, scratch, commands, shell, last message, log)")
