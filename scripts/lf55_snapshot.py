#!/usr/bin/env python3
"""Shell-write tracking and undo for lean-forge-55.

Claude Code's checkpoints only cover its file-editing tools, and Opus 5.5 edits files through Bash.
Before each Bash call the hook records a manifest (size, mtime) of the working tree; after it, the diff
names the files the command changed. The first Bash call of a turn also stores an archive of the tree,
which `undo` restores. Files created during the turn are moved aside, never deleted.

usage: lf55_snapshot.py undo --session <id>     restore the tree to its state before the last turn's shell work
       lf55_snapshot.py list --session <id>     show stored turn snapshots
ponytail: size/mtime manifest, whole-tree tar per turn; capped, skipped (with a notice) above the cap.
"""
import hashlib, json, os, shutil, subprocess, sys, tarfile, time

DIR = os.path.expanduser("~/.cache/lean-forge-55/snapshots")
SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", ".next", ".cache", "dist", "build", "target", ".tox"}
MAX_FILES, MAX_BYTES = 5000, 30 * 1024 * 1024


def root_of(cwd):
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, capture_output=True, text=True, timeout=3)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return os.path.realpath(cwd)


def manifest(root):
    """{relative path: [size, mtime_ns]}, or None when the tree is over the file cap."""
    out = {}
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP]
        for f in files:
            p = os.path.join(d, f)
            try:
                s = os.lstat(p)
            except OSError:
                continue
            out[os.path.relpath(p, root)] = [s.st_size, s.st_mtime_ns]
            if len(out) > MAX_FILES:
                return None
    return out


def _sdir(sid):
    d = os.path.join(DIR, sid)
    os.makedirs(d, exist_ok=True)
    return d


def before(sid, call_id, cwd, turn):
    """Record the pre-command manifest; archive the tree once per turn. Returns a notice or ''."""
    root = root_of(cwd)
    d = _sdir(sid)
    big = os.path.join(d, "too-big-" + hashlib.sha1(root.encode()).hexdigest()[:12])  # stable across hook processes
    if os.path.exists(big):
        return ""  # said once for this session; do not rescan a tree over the cap on every command
    m = manifest(root)
    if m is None:
        open(big, "w").write(root)
        return f"lean-forge-55: the tree under {root} has over {MAX_FILES} files; shell changes are not tracked and undo is off for this session."
    json.dump({"root": root, "files": m}, open(os.path.join(d, f"pre-{call_id}.json"), "w"))
    tag = f"turn-{turn}"
    if os.path.exists(os.path.join(d, tag + ".json")):
        return ""
    if sum(v[0] for v in m.values()) > MAX_BYTES:
        return f"lean-forge-55: the tree is over {MAX_BYTES // 2**20} MB; no undo snapshot for this turn (git is the way back)."
    with tarfile.open(os.path.join(d, tag + ".tar.gz"), "w:gz") as t:
        for rel in m:
            try:
                t.add(os.path.join(root, rel), arcname=rel, recursive=False)
            except OSError:
                pass
    json.dump({"root": root, "files": m, "at": time.time()}, open(os.path.join(d, tag + ".json"), "w"))
    json.dump({"tag": tag}, open(os.path.join(d, "latest.json"), "w"))
    return ""


def after(sid, call_id):
    """Absolute paths the command created or modified, and those it removed."""
    p = os.path.join(DIR, sid, f"pre-{call_id}.json")
    try:
        pre = json.load(open(p))
    except Exception:
        return [], []
    os.remove(p)
    root, old = pre["root"], pre["files"]
    new = manifest(root) or {}
    changed = [os.path.join(root, r) for r, v in new.items() if old.get(r) != v]
    removed = [os.path.join(root, r) for r in old if r not in new]
    return changed, removed


def undo(sid):
    d = os.path.join(DIR, sid)
    try:
        tag = json.load(open(os.path.join(d, "latest.json")))["tag"]
        meta = json.load(open(os.path.join(d, tag + ".json")))
    except Exception:
        print("lean-forge-55: no shell snapshot for this session."); return 1
    root, snap = meta["root"], meta["files"]
    now = manifest(root) or {}
    aside = os.path.join(d, f"undo-{int(time.time())}")
    # the undo itself is undoable: keep what we are about to overwrite or move
    created = [r for r in now if r not in snap]
    changed = [r for r in snap if now.get(r) != snap[r]]
    for rel in created + [r for r in changed if r in now]:
        dst = os.path.join(aside, "before-undo", rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(root, rel), dst)
    for rel in created:
        os.remove(os.path.join(root, rel))  # a copy is kept in the aside folder above
    with tarfile.open(os.path.join(d, tag + ".tar.gz")) as t:
        members = [m for m in t.getmembers() if m.name in set(changed)]
        t.extractall(root, members=members, filter="data")
    print(json.dumps({"restored_to": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(meta["at"])), "root": root,
                      "restored": changed, "moved_aside": created, "aside_folder": aside}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[2] == "--session":
        if sys.argv[1] == "undo":
            sys.exit(undo(sys.argv[3]))
        if sys.argv[1] == "list":
            d = os.path.join(DIR, sys.argv[3])
            for f in sorted(x for x in os.listdir(d) if x.startswith("turn-") and x.endswith(".json")) if os.path.isdir(d) else []:
                m = json.load(open(os.path.join(d, f)))
                print(f[:-5], time.strftime("%H:%M:%S", time.localtime(m["at"])), m["root"], len(m["files"]), "files")
            sys.exit(0)
    print(__doc__); sys.exit(2)
