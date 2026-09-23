#!/usr/bin/env python3
"""lean-forge-55 SETTLE gate (Jev triage). PROVE is Castra's evidence ledger (castra-trace/openloop).

Opus 5.5 edits files through Bash, which Claude Code's edit hooks and checkpoints never see. For that model
(or while the model is still unknown, at a session's first tool call) the gate also closes shell writes,
and every Bash call is diffed against the tree so changed files enter the Castra ledger and can be undone.
Other models get plain lean-forge behavior.

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
JEV_Q = {"fixed": {"type": "noul", "instructions":
    "Given the request and the repository summary, does the request literally determine the exact result, so that any two "
    "competent engineers who have never talked to this user would produce the same observable output (same names, formats, "
    "rules, edge-case behavior)? Answer yes only if no product, policy, or output-format decision is left open."}}
MECH_THRESHOLD = 0.5  # ponytail: calibrated on 16 requests (mechanical >= 0.55, must-ask <= 0.21); tune on real traffic
REPLY_Q = {"reply": {"type": "noul", "instructions":
    "The agent's last message and the user's new message are given. Is the user's new message a reply to the agent's "
    "last message (answering its questions, choosing among its options, approving or correcting its proposal), rather than "
    "a new, separate request about something else?"}}
CODE_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".rb", ".sh", ".bash", ".zsh", ".java", ".kt", ".swift",
                 ".c", ".cpp", ".css", ".scss", ".html", ".vue", ".svelte", ".ipynb", ".sql", ".yml", ".yaml"}  # Castra's set + config
REPLY_THRESHOLD = 0.5  # ponytail: calibrated on 10 exchanges (reply >= 0.79, new request <= 0.24)


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


def jev_fixed(prompt, cwd):
    """Probability that the request fixes the result."""
    try:
        files = subprocess.run(["git", "ls-files"], cwd=cwd, capture_output=True, text=True, timeout=2).stdout.split()[:60]
    except Exception:
        files = []
    return jev({"repository_files": files, "request": prompt[:6000]}, JEV_Q, "fixed")


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


def is_reply(prompt, transcript_path):
    """After a turn that ended without edits: is this message an answer, not a new request? Unknown counts as answer."""
    agent = last_agent_message(transcript_path)
    if not agent:
        return True
    p = jev({"agent_last_message": agent, "user_new_message": prompt[:4000]}, REPLY_Q, "reply")
    return p is None or p >= REPLY_THRESHOLD


# A shell command that writes into the project: a redirect to a file path, an in-place editor, a file-writing call,
# or a git/file operation that changes the tree. Temp and device targets are fine. ponytail: string heuristic for the
# gate only; the pre/post tree diff is what actually records changes.
REDIRECT = re.compile(r"(?<![-=<>&0-9])>{1,2}\s*(?!&)(['\"]?)([^\s'\";|&)]+)")
TARGET = re.compile(r"(/|[A-Za-z_][\w-]*\.[A-Za-z]{1,8}$)")
WRITERS = re.compile(r"\b(sed\s+-i|perl\s+-\w*i|tee\b|truncate\b|touch\b|cp\b|mv\b|rm\b|install\b|patch\b|"
                     r"git\s+(apply|checkout|restore|reset|stash|commit|merge|rebase|am|cherry-pick))|"
                     r"open\([^)]*['\"][wax]|write_text\(|write_bytes\(|writeFileSync|fs\.writeFile")
SAFE = ("/tmp/", "/private/tmp/", "/dev/", "/var/folders/")


def writes_files(cmd):
    for _, t in REDIRECT.findall(cmd):
        if TARGET.search(t) and not t.startswith(SAFE):
            return True
    return bool(WRITERS.search(cmd))


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


def is55(model):
    return model is None or "opus-5-5" in model


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
        model = st.get("model")
        if st.get("state") == "asked" and is_reply(inp.get("prompt", ""), inp.get("transcript_path", "")):
            st = {"state": "open", "prompt_at": now, "jev": st.get("jev"), "model": model}  # the user is answering our questions
        else:  # a new request, including one that follows a turn which only explained something
            p = jev_fixed(inp.get("prompt", ""), inp.get("cwd", "."))
            st = {"state": "open" if (p is not None and p >= MECH_THRESHOLD) else "closed",
                  "prompt_at": now, "jev": p, "hatch": p is None, "model": model}

    elif ev == "pre" and inp.get("tool_name") == "Bash":
        if not is55(current_model(inp, st)):
            return json.dump(st, open(sp, "w"))
        cmd = (inp.get("tool_input") or {}).get("command", "")
        if st.get("state") != "open" and writes_files(cmd):
            json.dump(st, open(sp, "w"))
            return deny("lean-forge-55 SETTLE: file writes are closed, shell writes included, until the outcome-changing "
                        "decisions are settled. Reading, searching and running tests stay open. Send the user your "
                        "questions (recommendation + a boundary example each) and end your turn.")
        notice = snap.before(sid, inp.get("tool_use_id", "x"), inp.get("cwd", "."), st.get("prompt_at", 0))
        if notice:
            context("PreToolUse", notice)

    elif ev == "post":
        if inp.get("tool_name") != "Bash" or not is55(current_model(inp, st)):
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
