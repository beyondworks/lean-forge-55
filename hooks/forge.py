#!/usr/bin/env python3
"""lean-forge-55 SETTLE gate (Jev triage). PROVE is Castra's evidence ledger (castra-trace/openloop).

When Claude Code steers the session to Bash-first file work (its `bashFirst` mode, seen in the transcript), Claude edits
files through Bash, which Claude Code's edit hooks and checkpoints never see; this was measured on Opus 5.5, Opus 5 and
Fable 5.1 alike. In such sessions (or while the transcript does not show yet) the gate also closes shell writes, and every
Bash call is diffed against the tree so changed files enter the Castra ledger and can be undone. Sessions without
bashFirst get plain lean-forge behavior. The Opus 5.5 prompt rules live in protocol-55.md.

A message after a turn that ended closed (the agent asked) opens the gate unless Jev judges it a new, unrelated task;
that one is triaged from scratch. A message that arrives while the agent is still working, a message from another
session and a background-task notice leave the gate as it is. If Jev is unavailable, the gate keeps what it was
(open work stays open) and a one-line marker file opens a closed mechanical request.

State is one JSON file per session, replaced atomically; every hook event appends one line to log.jsonl (no message
text, only lengths and hashes; lines older than 30 days are pruned).
"""
import hashlib, json, os, random, re, subprocess, sys, time, urllib.request
from datetime import datetime
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "scripts")]
import lf55_snapshot as snap

try:  # the log names the release that made each decision
    VERSION = json.load(open(Path(__file__).resolve().parents[1] / ".claude-plugin" / "plugin.json"))["version"]
except Exception:
    VERSION = "?"
STATE_DIR = os.environ.get("LF55_STATE_DIR") or os.path.expanduser("~/.cache/lean-forge-55")
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch"}  # apply_patch: Codex
LOG_ONLY = os.environ.get("LF55_LOG_ONLY") == "1"  # measure without acting: no denials, notes or undo copies
WOULD = []  # what the hook would have said, in LOG_ONLY
NEEDS_Q = {"needs_decision": {"type": "noul", "instructions":
    "The user's new message is a request to a coding agent; the agent's last message and a note on how this user "
    "works are given as context. Judge only the work the agent must carry out now. To carry it out, must the agent "
    "choose something the user will see or rely on that neither the message nor the conversation settles: an output "
    "format, names or labels, a screen or interaction design, a policy or business rule, or one of several "
    "meaningfully different behaviors? Answer no when the work is fixing a reported problem so things work as "
    "intended, investigating, checking, running, testing, committing, deploying, publishing, restarting, processing a "
    "named file with stated parameters, following a standing procedure, or applying a change the user described "
    "concretely or the agent already proposed. Also answer no when the user answers the agent's questions or picks "
    "among its options, even if the answer adds constraints; when the user leaves the choices to the agent (for "
    "example 'your call', 'go with your recommendation', '너네가 결정해', '알아서 해', '권장안대로'); when the user asks to "
    "see a draft, mockup or sample before deciding; when the work is writing a document for the user to review (a "
    "plan, proposal, report, brief, draft, mockup or wireframe), since the agent writes it with its recommendations "
    "and lists the open choices inside it; and when the message tells the agent to go ahead with settled work "
    "and also asks questions or floats ideas, since those are answered in words, not built."}}
NEW_Q = {"new_task": {"type": "noul", "instructions":
    "The agent's last message ended its turn waiting for the user: it asked questions, offered options or asked for "
    "approval. Is the user's new message a new, unrelated task rather than a reply to that message (an answer, a "
    "choice, a correction, a go-ahead, or a follow-up question about it)?"}}
# ponytail: calibrated on the author's own messages: 100 labeled to choose the question and threshold, 100 fresh ones held
# out (must-ask scored 0.85-0.96; wrongly closed 1 of 42, wrongly opened 0 of 5). Re-check on your own traffic.
# 2026-09-28: the answer / hand-off / go-ahead-plus-question clauses were added after 25 real messages the gate closed wrongly
# (answers with added constraints, "권장안대로", "커밋해줘. 그리고 …?"): wrongly closed 16-19/25 -> 7/25, held-out unchanged.
# 0.2.4: "show me a draft first" opens (a draft is how such questions get answered), and a reply to an asking turn opens
# unless NEW_Q says it is a new task. The 85 real messages after an asking turn: 36 changed the result through the reply
# itself, so asking happened; opening on the reply only skips a second, redundant round.
NEEDS_THRESHOLD = 0.8
PROFILE = os.path.expanduser("~/.config/lean-forge-55/profile.txt")  # optional, private: how this user instructs agents
CONFIG = os.environ.get("LF55_CONFIG") or os.path.expanduser("~/.config/lean-forge-55/config.json")  # optional, private: {"open_commands": [...], "scratch_paths": [...]}
CODE_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".rb", ".sh", ".bash", ".zsh", ".java", ".kt", ".swift",
                 ".c", ".cpp", ".css", ".scss", ".html", ".vue", ".svelte", ".ipynb", ".sql", ".yml", ".yaml"}  # Castra's set + config
PEER = ("Another Claude session sent a message", "<cross-session-message")


def config():
    try:
        return json.load(open(CONFIG))
    except Exception:
        return {}


def jev(state, questions, name=None):
    """Jev probabilities {question: p} (or one p when `name` is given), or None when Jev is unavailable (callers fall back).
    LEAN_FORGE_JEV=off switches Jev off; =fixed:a=0.1,b=0.9 answers fixed values (tests)."""
    mode = os.environ.get("LEAN_FORGE_JEV", "")
    if mode == "off":
        return None
    if mode.startswith("fixed:"):
        got = {k: float(v) for k, v in (kv.split("=") for kv in mode[6:].split(","))}
        return got.get(name) if name else got
    try:
        key = os.environ.get("TYPESAFE_API_KEY") or subprocess.run(
            ["security", "find-generic-password", "-s", "TYPESAFE_API_KEY", "-w"],  # macOS keychain
            capture_output=True, text=True, timeout=2).stdout.strip()
        if not key:
            return None
        body = json.dumps({"model": "jev-latest", "questions": questions, "state": state}).encode()
        req = urllib.request.Request("https://api.typesafe.ai/v1/systemone", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                              "User-Agent": "lean-forge"})  # the API's edge blocks Python-urllib's default UA (403, error 1010)
        with urllib.request.urlopen(req, timeout=3) as r:  # the prompt hook has 8 s, and a busy machine eats some of it
            got = {k: v["noul"] for k, v in json.load(r)["answers"].items()}
        return got.get(name) if name else got
    except Exception:
        return None


def judge(prompt, agent, asked):
    """Jev answers for this message: needs_decision, plus new_task when the last turn was waiting for the user."""
    state = {"agent_last_message": agent[-3000:], "user_new_message": prompt[:4000]}
    try:
        state["about_this_user"] = open(PROFILE).read().strip()[:2000]
    except OSError:
        pass
    return jev(state, dict(NEEDS_Q, **NEW_Q) if asked else NEEDS_Q)


def needs_decision(prompt, agent):
    """Probability that the request leaves a user-visible decision open (kept for the private calibration scripts)."""
    got = judge(prompt, agent, False)
    return got and got.get("needs_decision")


def text_of(content):
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


ASKS = re.compile(r"\?|？|알려 ?주|정해 ?주|말씀해 ?주|답해 ?주|골라 ?주|확인해 ?주")


def tail(transcript_path, size=400_000):
    try:
        with open(transcript_path, "rb") as f:
            f.seek(0, 2); f.seek(max(0, f.tell() - size))
            return f.read().decode("utf-8", "replace").splitlines()
    except Exception:
        return []


IDLE = 15 * 60  # ponytail: activity older than this is a turn that ended without a recorded Stop (app restart, resume)


def mid_turn(transcript_path):
    """True when the agent is still working: its latest activity comes after the last turn end (stop_hook_summary)
    in the transcript and is recent. A turn started by a task notice or continued after a Stop-hook block counts;
    a user interrupt or a session (re)start ends the turn."""
    for line in reversed(tail(transcript_path)):
        try:
            m = json.loads(line)
        except Exception:
            continue
        t, content = m.get("type"), (m.get("message") or {}).get("content")
        if t == "system" and m.get("subtype") == "stop_hook_summary":
            return False
        if t == "attachment" and (m.get("attachment") or {}).get("hookEvent") == "SessionStart":
            return False
        tool_result = isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
        text = "" if t != "user" or tool_result else text_of(content).lstrip()
        if t == "assistant" or tool_result or text.startswith("Stop hook feedback"):
            try:
                at = datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00")).timestamp()
            except Exception:
                return True
            return time.time() - at < IDLE
        if text.startswith("[Request interrupted"):
            return False
    return False


def last_agent_message(transcript_path, prompt=""):
    """What the agent said since the user's last message ('' if unreadable): the most recent block that asks something,
    else the last block. (A closing line such as "1 check pending" often follows the question, e.g. after a Stop-hook
    nudge; the question may also be in that closing message itself, so nothing is dropped.)"""
    blocks = []
    for line in reversed(tail(transcript_path)):
        try:
            m = json.loads(line)
        except Exception:
            continue
        content = (m.get("message") or {}).get("content")
        if m.get("type") == "assistant" and isinstance(content, list):
            text = text_of(content).strip()
            if text:
                blocks.append(text)
        elif m.get("type") == "user" and not (isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content)):
            text = text_of(content).strip()
            if text and not m.get("isMeta") and not text.startswith("Stop hook feedback") and not (not blocks and text == prompt.strip()):
                break  # the user's previous message: the agent's turn starts after it
    asking = [b for b in blocks if ASKS.search(b[-600:])]
    best = asking[0] if asking else (blocks[0] if blocks else "")
    return best[-4000:]


# A shell command that writes into the project: a redirect to a file path, an in-place editor, a file-writing call,
# or a git operation that changes the tree. Writes outside the project (temp files, caches, the user's own notes) and
# targets that cannot be resolved from the text are not gated. ponytail: string heuristic for the gate only; the
# pre/post tree diff is what actually records changes inside the project.
# every operator that sends output to a file: >, >>, >|, N>, N>>, &>, &>>, >&file (not a descriptor copy like 2>&1).
# ponytail: read as bash would read a command line, here-document bodies included. Tag text in an HTML body such as
# "<b>10/07" then reads as a write when the gate is closed; that is accepted, because telling data from commands
# needs a full shell parser (three review rounds found differentials), and document requests keep the gate open.
REDIRECT = re.compile(r"(?:(?<![-=<>&])\d*>>?\|?|&>>?|(?<![-=<>&])\d*>&(?![\d-]))\s*(['\"]?)([^\s'\";|&)<>]+)")
TARGET = re.compile(r"(/|[A-Za-z_][\w-]*\.[A-Za-z]{1,8}$)")
OPEN = re.compile(r"(?:open|Path)\(\s*([fbr]{0,2})(['\"])(.*?)\2\s*(?:,\s*[a-z]*\s*=?\s*['\"][wax]|\)\s*\.(?:write_text|write_bytes|open\(\s*['\"][wax]))")
FS_WRITE = re.compile(r"(?:writeFileSync|fs\.writeFile)\(\s*(['\"])(.*?)\1")
WORDS = re.compile(r"(?:^|[\s;&|(])(sed\s+-i|perl\s+-\w*i\w*|tee|truncate|touch|cp|mv|rm|install|patch)\b([^;&|\n]*)")
# git that overwrites or discards work in the tree; commits, merges, rebases and new branches are the user's to name
# (protocol rule 2) and the guardian handles force pushes, so they are not SETTLE's business (0.2.4).
GIT = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?(apply|restore|reset|stash|am|checkout\b(?!\s+(?:-q\s+)?-[bB]\b))")
CD = re.compile(r"(?:^|[;&|(]\s*)cd\s+(['\"]?)([^\s'\";|&)]+)\1")
ASSIGN = re.compile(r"(?:^|[;&|(\s])([A-Za-z_]\w*)=(['\"]?)([^\s'\";&|)]+)\2(?=[\s;&|)]|$)")


def subst(text, cmd, pos):
    """Expand $NAME / ${NAME} from literal `NAME=value` assignments earlier in the same command."""
    if "$" not in text:
        return text
    env = {m.group(1): m.group(3) for m in ASSIGN.finditer(cmd, 0, pos)}
    return re.sub(r"\$\{?([A-Za-z_]\w*)\}?", lambda m: env.get(m.group(1), m.group(0)), text)


def scratch(path, root):
    """Paths that hold drafts, notes and handovers rather than the result: writable while SETTLE is closed."""
    p = os.path.realpath(os.path.expanduser(path))
    dirs = ["/tmp", "/private/tmp", os.environ.get("TMPDIR", "/tmp"), "~/.claude/plans"]
    dirs += config().get("scratch_paths", [])
    if any(p == d or p.startswith(d.rstrip("/") + "/") for d in (os.path.realpath(os.path.expanduser(x)) for x in dirs)):
        return True
    if re.match(re.escape(os.path.realpath(os.path.expanduser("~/.claude/projects"))) + r"/[^/]+/memory/", p):
        return True
    return p == os.path.join(os.path.realpath(root), "SESSION_HANDOVER.md")


def inside(path, root, base=None):
    """True when a literal path points into the project's result; False outside it, in scratch places, or when it
    cannot be resolved. A relative path is read from `base` (the command's own last `cd`), else from the session's
    working directory."""
    path = path.strip().strip("'\"")
    if not path or "{" in path or "$" in path or "*" in path:
        return False
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        if base is None:
            return True  # relative to the session's working directory
        path = os.path.join(base, path)
    root = os.path.realpath(root)
    real = os.path.realpath(path)
    return (real == root or real.startswith(root + os.sep)) and not scratch(real, root)


def base_at(cmd, pos, root):
    """Directory a relative path at `pos` is read from: the last `cd` before it, or None (the session's directory).
    ponytail: follows literal `cd` and `NAME=value` variables; a `cd` inside a subshell or from elsewhere falls back."""
    base = None
    for m in CD.finditer(cmd, 0, pos):
        d = os.path.expanduser(subst(m.group(2), cmd, m.start()))
        if "$" in d or d == "-":
            return None
        base = d if os.path.isabs(d) else os.path.join(base or root, d)
    return base


def writes_files(cmd, root):
    """The part of the command that writes into the project ('' when none): shown to the agent when it is denied.
    The whole text is read, here-document bodies included (a body may be run: piped to sh, eval'd, fed to python)."""
    at = lambda m, g, path=None: inside(subst(path if path is not None else m.group(g), cmd, m.start()), root,
                                        base_at(cmd, m.start(), root))
    for m in REDIRECT.finditer(cmd):
        # the target ends at < or > as in bash (they start the next redirect): HTML "> </head>" has no target,
        # ">app/a<b.py" writes app/a
        target = m.group(2)
        if TARGET.search(target) and at(m, 2, target):
            return m.group(0).strip()
    for m in list(OPEN.finditer(cmd)) + list(FS_WRITE.finditer(cmd)):
        if (m.re is OPEN and not m.group(1) and at(m, 3)) or (m.re is FS_WRITE and at(m, 2)):
            return m.group(0)[:120]
    for m in WORDS.finditer(cmd):
        word, base = m.group(1), base_at(cmd, m.start(), root)
        args = [subst(a, cmd, m.start()) for a in m.group(2).split() if not a.startswith("-")]
        if word.startswith("sed") and args:
            args = args[1:]  # the sed script, not a path
        if word == "cp":
            args = args[-1:]  # only the destination changes
        if any(inside(a, root, base) for a in args):
            return m.group(0).strip()[:120]
    g = GIT.search(cmd)
    return g.group(0) if g else ""


def current_model(inp, st):
    """The session's model: cached, else the last assistant message in the transcript (None at the very start)."""
    try:
        with open(inp.get("transcript_path", ""), "rb") as f:
            f.seek(0, 2); f.seek(max(0, f.tell() - 200_000))
            for line in reversed(f.read().decode("utf-8", "replace").splitlines()):
                try:
                    m = json.loads(line)
                except Exception:
                    continue
                if m.get("type") == "assistant" and (m.get("message") or {}).get("model"):
                    st["model"] = m["message"]["model"]  # follows /model switches
                    break
                if m.get("type") == "turn_context" and (m.get("payload") or {}).get("model"):
                    st["model"] = m["payload"]["model"]  # Codex
                    break
    except OSError:
        pass
    return st.get("model")


def bash_first(inp, st):
    """True/False once the transcript shows Claude Code's auto_mode attachment; None before it is written."""
    if st.get("bash_first") is not None:
        return st["bash_first"]
    try:
        with open(inp.get("transcript_path", ""), "rb") as f:
            head = f.read(2_000_000).decode("utf-8", "replace")
    except OSError:
        return None
    for line in head.splitlines():
        if '"auto_mode"' in line:
            try:
                st["bash_first"] = bool(json.loads(line)["attachment"].get("bashFirst"))
                return st["bash_first"]
            except Exception:
                continue
    if '"type":"assistant"' in head.replace(" ", ""):
        st["bash_first"] = False  # the session is under way and no bashFirst steering was recorded
        return False
    return None


def shell_rules(inp, st):
    current_model(inp, st)  # cached for reporting
    return bash_first(inp, st) is not False


def context(event, text):
    if LOG_ONLY:
        return WOULD.append("note")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))


HANGUL = re.compile(r"[가-힣]")


def denial(st, caught, en):
    """Denial text: in Korean when the user writes Korean, with what was caught and the one way to continue."""
    if st.get("lang") != "ko":
        return f"{en} Caught: {caught}"
    return ("[lean-forge 정하기] 결과를 바꾸는 결정이 아직 정해지지 않아 쓰기를 막았습니다.\n"
            f"걸린 것: {caught}\n"
            "계속하려면: 사용자에게 질문(추천안과 경계 예시)을 보내고 턴을 끝내세요. 읽기·검색·테스트 실행과 scratchpad 초안 "
            "쓰기는 열려 있습니다. 사용자가 답하면 열립니다.\n"
            "사용자에게는 사용자 언어로 짧게 알리세요. 특정 단어를 다시 보내 달라고 하지 마세요.")


def deny(reason):
    if LOG_ONLY:
        return WOULD.append("deny")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))


def load(sp):
    """The session's state; None when it has none yet. An unreadable file (half-written by a killed pre-0.2.4 hook)
    is read again, then treated as open: losing the state must not lock the session."""
    for _ in range(3):
        try:
            return json.load(open(sp))
        except FileNotFoundError:
            return None
        except Exception:
            time.sleep(0.02)
    return {"state": "open", "prompt_at": 0, "recovered": True}


def save(sp, st):
    tmp = f"{sp}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, sp)  # atomic: a reader sees the old state or the new one, never half of it


def log(**rec):
    """One line per hook event, for measuring the gate on real traffic. No message text."""
    path = f"{STATE_DIR}/log.jsonl"
    try:
        if random.random() < 0.01 and os.path.exists(path):  # prune lines older than 30 days now and then
            cut = time.time() - 30 * 86400
            keep = [l for l in open(path) if json.loads(l).get("t", 0) >= cut]
            with open(path + ".tmp", "w") as f:
                f.writelines(keep)
            os.replace(path + ".tmp", path)
        with open(path, "a") as f:
            f.write(json.dumps(dict(t=round(time.time(), 3), v=VERSION, **rec), ensure_ascii=False) + "\n")
    except Exception:
        pass


def origin(prompt):
    """What submitted this prompt: a background-task notice, another session, a slash command, or the user."""
    p = prompt.lstrip()
    if p.startswith("<task-notification>"):
        return "task"
    if p.startswith(PEER):
        return "peer"
    m = re.match(r"/([\w:.-]+)(?=\s|$)", p) or re.search(r"<command-name>/([\w:.-]+)</command-name>", p)
    return f"/{m.group(1)}" if m else "user"


def main():
    t0 = time.time()
    ev = sys.argv[1]
    inp = json.load(sys.stdin)
    sid = inp.get("session_id", "unknown")
    os.makedirs(STATE_DIR, exist_ok=True)
    sp, mech = f"{STATE_DIR}/{sid}.json", f"{STATE_DIR}/{sid}.mechanical"
    st = load(sp) or {"state": "closed", "prompt_at": 0}
    now = time.time()
    off = os.path.exists(f"{STATE_DIR}/{sid}.off")  # the user switched the gate off for this session only
    if off:
        st["state"] = "open"
    rec = {"sid": sid[:8], "ev": ev, "tool": inp.get("tool_name"), "before": st.get("state"), "model": st.get("model"),
           "keys": sorted(inp)}

    def done(**more):
        if LOG_ONLY:
            more.update(log_only=True, would=WOULD[:] or None, model=current_model(inp, st))
        rec.update(more, after=st.get("state"), ms=round((time.time() - t0) * 1000))
        log(**rec)

    if ev == "prompt":
        prompt = inp.get("prompt", "")
        kind = origin(prompt)
        rec.update(origin=kind, len=len(prompt), h=hashlib.sha1(prompt.encode()).hexdigest()[:10])
        if kind in ("task", "peer"):
            return done(rule="not-the-user")  # a notice or another session's message is not a request: state kept
        if st.get("state") == "open" and mid_turn(inp.get("transcript_path", "")):
            return done(rule="queued-while-working")  # the agent is mid-turn on settled work; this joins it
        prev, prev_at = st.get("state"), st.get("prompt_at")
        base = {"prompt_at": now, "model": st.get("model"), "bash_first": st.get("bash_first"),
                "lang": "ko" if HANGUL.search(prompt) else "en"}
        if off or kind.lstrip("/") in config().get("open_commands", []):
            st = dict(base, state="open", jev=None, hatch=False)
            save(sp, st)
            return done(rule="off" if off else "standing-command")
        # Saved before Jev is asked, so a hook killed mid-call (busy machine, 8 s limit) leaves a sane state:
        # an answer or continuing work stays open, a closed request stays closed with the marker hatch.
        st = dict(base, state="open" if prev in ("open", "asked") else "closed", jev=None, hatch=True)
        save(sp, st)
        agent = (inp.get("last_assistant_message") or "" if "last_assistant_message" in inp  # Codex passes it
                 else last_agent_message(inp.get("transcript_path", ""), prompt)) if prev_at else ""
        tj = time.time()
        got = judge(prompt, agent, prev == "asked")
        rec.update(jev_ms=round((time.time() - tj) * 1000), agent_len=len(agent))
        if got is None:
            return done(rule="jev-unavailable")
        p, p_new = got.get("needs_decision"), got.get("new_task")
        if prev == "asked" and p_new is not None and p_new < NEEDS_THRESHOLD:
            opened, rule = True, "reply-to-question"
        else:
            opened, rule = p is not None and p < NEEDS_THRESHOLD, "jev"
        st.update(state="open" if opened else "closed", jev=p, jev_new=p_new, hatch=False)
        save(sp, st)
        return done(rule=rule, p=p, p_new=p_new)

    elif ev == "pre" and inp.get("tool_name") == "Bash":
        if not shell_rules(inp, st):
            save(sp, st)
            return done()
        cmd = (inp.get("tool_input") or {}).get("command", "")
        caught = st.get("state") != "open" and writes_files(cmd, snap.root_of(inp.get("cwd", ".")))
        if caught:
            save(sp, st)
            deny(denial(st, caught, "lean-forge-55 SETTLE: file writes are closed, shell writes included, until the "
                        "outcome-changing decisions are settled. Reading, searching, running tests and writing drafts to "
                        "the scratchpad stay open. Send the user your questions (recommendation + a boundary example "
                        "each) and end your turn."))
            return done(deny="settle-shell")
        if LOG_ONLY:
            save(sp, st)
            return done()
        notice = snap.before(sid, inp.get("tool_use_id", "x"), inp.get("cwd", "."), st.get("prompt_at", 0))
        if notice:
            context("PreToolUse", notice)
        save(sp, st)
        return done()

    elif ev == "post" and inp.get("tool_name") == "AskUserQuestion":
        st["state"] = "open"  # the user answered the agent's questions inside the tool
        save(sp, st)
        return done(rule="answered-in-tool")

    elif ev == "post":
        if LOG_ONLY or inp.get("tool_name") != "Bash" or not shell_rules(inp, st):
            return
        changed, removed = snap.after(sid, inp.get("tool_use_id", "x"))
        if not changed and not removed:
            return
        try:
            import castra_runtime as runtime  # vendored Castra: shell-written files enter the ledger as pending
            cs = runtime.session_id(sid)
            for f in changed:
                if Path(f).suffix.lower() in CODE_SUFFIXES:
                    runtime.record_edit(inp.get("cwd", "."), cs, Path(f), inp.get("agent_id"))
        except Exception:
            pass
        names = ", ".join(os.path.relpath(f, inp.get("cwd", ".")) for f in (changed + removed)[:8])
        if st.get("state") != "open":
            context("PostToolUse", f"lean-forge-55: this command changed files while writes are closed ({names}). "
                    "Do not continue building: tell the user, and offer to undo (lean-forge-55 undo).")
        else:
            context("PostToolUse", f"lean-forge-55: shell changes recorded for Castra verification and undo: {names}.")
        return done(changed=len(changed) + len(removed))

    elif ev == "pre":
        if inp.get("tool_name") not in EDIT_TOOLS or st.get("state") == "open":
            return
        ti = inp.get("tool_input") or {}
        paths = [ti.get("file_path") or ti.get("notebook_path") or ""]
        if inp.get("tool_name") == "apply_patch":  # Codex: the patch names its files
            text = ti.get("command") or ti.get("input") or ti.get("patch") or ""
            paths = [os.path.join(inp.get("cwd", "."), p.strip()) if not os.path.isabs(p.strip()) else p.strip()
                     for p in re.findall(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$|^\*\*\* Move to: (.+)$", str(text), re.M)
                     for p in p if p] or [""]
        path = paths[0]
        if all(paths) and all(scratch(p, snap.root_of(inp.get("cwd", "."))) for p in paths):
            return done(rule="scratch")
        if not st.get("hatch", True):
            deny(denial(st, path, "lean-forge SETTLE: an independent check judged that this request leaves "
                        "outcome-changing decisions open. Send the user your questions / confirm-by-example message and "
                        "end your turn; edits open when they answer. Drafts in the scratchpad stay writable."))
            return done(deny="settle-jev")
        try:
            if os.path.getmtime(mech) >= st["prompt_at"] and open(mech).read().strip():
                return done(rule="marker")
        except OSError:
            pass
        deny("lean-forge SETTLE: edits are closed until the outcome-changing decisions are settled. Either "
             "(a) send the user your questions and end your turn, or (b) if the request literally fixes the "
             f"result, run `echo '<one-line reason>' > {mech}` and retry the edit.")
        return done(deny="settle-fallback")

    elif ev == "stop":
        current_model(inp, st)
        used_hatch = (st.get("hatch", True) and os.path.exists(mech)  # a marker the gate refused (Jev answered) is not a used hatch
                      and os.path.getmtime(mech) >= st.get("prompt_at", 0))
        if st.get("state") == "closed" and not used_hatch:
            st["state"] = "asked"
        save(sp, st)
        return done()

    save(sp, st)


if __name__ == "__main__":
    main()
