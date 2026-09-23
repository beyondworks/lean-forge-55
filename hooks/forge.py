#!/usr/bin/env python3
"""lean-forge-55 SETTLE gate (Jev triage). PROVE is Castra's evidence ledger (castra-trace/openloop).

When Claude Code steers the session to Bash-first file work (its `bashFirst` mode, seen in the transcript), Claude edits
files through Bash, which Claude Code's edit hooks and checkpoints never see; this was measured on Opus 5.5, Opus 5 and
Fable 5.1 alike. In such sessions (or while the transcript does not show yet) the gate also closes shell writes, and every
Bash call is diffed against the tree so changed files enter the Castra ledger and can be undone. Sessions without
bashFirst get plain lean-forge behavior. The Opus 5.5 prompt rules live in protocol-55.md.

A message after a turn that ended without edits opens the gate only if Jev judges it a reply to that turn;
a new request there is triaged from scratch.

Per request, edits stay closed until the user answered our questions, unless Jev judges the request
mechanical. If Jev is unavailable, a one-line marker file opens a mechanical request (fallback).
ponytail: per-session JSON state, no locking; one session's hooks run sequentially.
"""
import json, os, re, subprocess, sys, time, urllib.request
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "scripts")]
import lf55_snapshot as snap

STATE_DIR = os.path.expanduser("~/.cache/lean-forge-55")
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
NEEDS_Q = {"needs_decision": {"type": "noul", "instructions":
    "The user's new message is a request to a coding agent; the agent's last message and a note on how this user works are "
    "given as context. To carry out the new message, must the agent choose something the user will see or rely on that "
    "neither the message nor the conversation settles: an output format, names or labels, a screen or interaction design, "
    "a policy or business rule, or one of several meaningfully different behaviors? Answer no when the work is fixing a "
    "reported problem so things work as intended, investigating, checking, running, testing, deploying, publishing, "
    "restarting, processing a named file with stated parameters, following a standing procedure, or applying a change the "
    "user described concretely or the agent already proposed."}}
# ponytail: calibrated on the author's own messages: 100 labeled to choose the question and threshold, 100 fresh ones held
# out (must-ask scored 0.85-0.96; wrongly closed 1 of 42, wrongly opened 0 of 5). Re-check on your own traffic.
NEEDS_THRESHOLD = 0.8
PROFILE = os.path.expanduser("~/.config/lean-forge-55/profile.txt")  # optional, private: how this user instructs agents
CODE_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".rb", ".sh", ".bash", ".zsh", ".java", ".kt", ".swift",
                 ".c", ".cpp", ".css", ".scss", ".html", ".vue", ".svelte", ".ipynb", ".sql", ".yml", ".yaml"}  # Castra's set + config


def jev(state, questions, name):
    """Jev probability for one noul question, or None when Jev is unavailable (callers fall back)."""
    if os.environ.get("LEAN_FORGE_JEV") == "off":
        return None
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
        with urllib.request.urlopen(req, timeout=4) as r:
            return json.load(r)["answers"][name]["noul"]
    except Exception:
        return None


def needs_decision(prompt, agent):
    """Probability that the request leaves a user-visible decision open, judged with the conversation and the user's habits."""
    state = {"agent_last_message": agent[-3000:], "user_new_message": prompt[:4000]}
    try:
        state["about_this_user"] = open(PROFILE).read().strip()[:2000]
    except OSError:
        pass
    return jev(state, NEEDS_Q, "needs_decision")


def last_agent_message(transcript_path):
    """Text of the last assistant message in the session transcript ('' if unreadable)."""
    try:
        with open(transcript_path, "rb") as f:
            f.seek(0, 2); f.seek(max(0, f.tell() - 400_000))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except Exception:
        return ""
    for line in reversed(lines):
        try:
            m = json.loads(line)
        except Exception:
            continue
        content = (m.get("message") or {}).get("content")
        if m.get("type") == "assistant" and isinstance(content, list):
            text = "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text").strip()
            if text:
                return text[-4000:]
    return ""


# A shell command that writes into the project: a redirect to a file path, an in-place editor, a file-writing call,
# or a git operation that changes the tree. Writes outside the project (temp files, caches, the user's own notes) and
# targets that cannot be resolved from the text are not gated. ponytail: string heuristic for the gate only; the
# pre/post tree diff is what actually records changes inside the project.
REDIRECT = re.compile(r"(?<![-=<>&0-9])>{1,2}\s*(?!&)(['\"]?)([^\s'\";|&)]+)")
TARGET = re.compile(r"(/|[A-Za-z_][\w-]*\.[A-Za-z]{1,8}$)")
OPEN = re.compile(r"(?:open|Path)\(\s*([fbr]{0,2})(['\"])(.*?)\2\s*(?:,\s*[a-z]*\s*=?\s*['\"][wax]|\)\s*\.(?:write_text|write_bytes|open\(\s*['\"][wax]))")
FS_WRITE = re.compile(r"(?:writeFileSync|fs\.writeFile)\(\s*(['\"])(.*?)\1")
WORDS = re.compile(r"(?:^|[\s;&|(])(sed\s+-i|perl\s+-\w*i\w*|tee|truncate|touch|cp|mv|rm|install|patch)\b([^;&|\n]*)")
GIT = re.compile(r"\bgit\s+(apply|checkout|restore|reset|stash|commit|merge|rebase|am|cherry-pick)\b")


def inside(path, root):
    """True when a literal path points into the project; False outside it or when it cannot be resolved."""
    path = path.strip().strip("'\"")
    if not path or "{" in path or "$" in path or "*" in path:
        return False
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        return True  # relative to the session's working directory
    root = os.path.realpath(root)
    return os.path.realpath(path) == root or os.path.realpath(path).startswith(root + os.sep)


def writes_files(cmd, root):
    if any(TARGET.search(t) and inside(t, root) for _, t in REDIRECT.findall(cmd)):
        return True
    if any(not f and inside(p, root) for f, _, p in OPEN.findall(cmd)) or any(inside(p, root) for _, p in FS_WRITE.findall(cmd)):
        return True
    for word, rest in WORDS.findall(cmd):
        args = [a for a in rest.split() if not a.startswith("-")]
        if word.startswith("sed") and args:
            args = args[1:]  # the sed script, not a path
        if word == "cp":
            args = args[-1:]  # only the destination changes
        if any(inside(a, root) for a in args):
            return True
    return bool(GIT.search(cmd))


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
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))


def main():
    ev = sys.argv[1]
    inp = json.load(sys.stdin)
    sid = inp.get("session_id", "unknown")
    os.makedirs(STATE_DIR, exist_ok=True)
    sp, mech = f"{STATE_DIR}/{sid}.json", f"{STATE_DIR}/{sid}.mechanical"
    try:
        st = json.load(open(sp))
    except Exception:
        st = {"state": "closed", "prompt_at": 0}
    now = time.time()

    if ev == "prompt":
        if "<task-notification>" in inp.get("prompt", ""):
            return  # a background-task notice, not a user request: the gate keeps its state
        model, bf = st.get("model"), st.get("bash_first")
        prompt = inp.get("prompt", "")
        agent = last_agent_message(inp.get("transcript_path", "")) if st.get("prompt_at") else ""
        p = needs_decision(prompt, agent)
        if p is None:  # Jev unavailable: only a message after an asking turn counts as its answer; otherwise the hatch
            opened = st.get("state") == "asked"
        else:  # continuing, approving or correcting the agent's plan, fixes and runs stay open; open-ended new work asks
            opened = p < NEEDS_THRESHOLD
        st = {"state": "open" if opened else "closed", "prompt_at": now, "jev": p, "hatch": p is None,
              "model": model, "bash_first": bf}

    elif ev == "pre" and inp.get("tool_name") == "Bash":
        if not shell_rules(inp, st):
            return json.dump(st, open(sp, "w"))
        cmd = (inp.get("tool_input") or {}).get("command", "")
        if st.get("state") != "open" and writes_files(cmd, snap.root_of(inp.get("cwd", "."))):
            json.dump(st, open(sp, "w"))
            return deny("lean-forge-55 SETTLE: file writes are closed, shell writes included, until the outcome-changing "
                        "decisions are settled. Reading, searching and running tests stay open. Send the user your "
                        "questions (recommendation + a boundary example each) and end your turn.")
        notice = snap.before(sid, inp.get("tool_use_id", "x"), inp.get("cwd", "."), st.get("prompt_at", 0))
        if notice:
            context("PreToolUse", notice)

    elif ev == "post":
        if inp.get("tool_name") != "Bash" or not shell_rules(inp, st):
            return
        changed, removed = snap.after(sid, inp.get("tool_use_id", "x"))
        if not changed and not removed:
            return
        try:
            import castra_runtime as runtime  # vendored Castra: shell-written files enter the ledger as pending
            cs = runtime.session_id(sid)
            for f in changed:
                if Path(f).suffix.lower() in CODE_SUFFIXES:
                    runtime.record_edit(inp.get("cwd", "."), cs, Path(f))
        except Exception:
            pass
        names = ", ".join(os.path.relpath(f, inp.get("cwd", ".")) for f in (changed + removed)[:8])
        if st.get("state") != "open":
            context("PostToolUse", f"lean-forge-55: this command changed files while writes are closed ({names}). "
                    "Do not continue building: tell the user, and offer to undo (lean-forge-55 undo).")
        else:
            context("PostToolUse", f"lean-forge-55: shell changes recorded for Castra verification and undo: {names}.")

    elif ev == "pre":
        if inp.get("tool_name") not in EDIT_TOOLS or st.get("state") == "open":
            return
        if not st.get("hatch", True):
            return deny("lean-forge SETTLE: an independent check judged that this request leaves outcome-changing "
                        "decisions open. Send the user your questions / confirm-by-example message and end your turn; "
                        "edits open when they answer.")
        try:
            if os.path.getmtime(mech) >= st["prompt_at"] and open(mech).read().strip():
                return
        except OSError:
            pass
        return deny("lean-forge SETTLE: edits are closed until the outcome-changing decisions are settled. Either "
                    "(a) send the user your questions and end your turn, or (b) if the request literally fixes the "
                    f"result, run `echo '<one-line reason>' > {mech}` and retry the edit.")

    elif ev == "stop":
        current_model(inp, st)
        used_hatch = (st.get("hatch", True) and os.path.exists(mech)  # a marker the gate refused (Jev answered) is not a used hatch
                      and os.path.getmtime(mech) >= st.get("prompt_at", 0))
        if st.get("state") == "closed" and not used_hatch:
            st["state"] = "asked"

    json.dump(st, open(sp, "w"))


main()
