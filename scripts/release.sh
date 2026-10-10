#!/usr/bin/env bash
# Release lean-forge-55 from a clean main: tests -> tag -> push -> GitHub release -> update the installed plugin ->
# refresh the copies running sessions still use -> prove the installed files match the tag.
#   scripts/release.sh            version from .claude-plugin/plugin.json; CHANGELOG.md needs a "## <version>" section
#   LF55_REPLAY="<command>"       optional: a replay of your own sessions to run before tagging (private data stays local)
set -euo pipefail
cd "$(dirname "$0")/.."
V=$(python3 -c 'import json; print(json.load(open(".claude-plugin/plugin.json"))["version"])')
T="v$V"
fail() { echo "release: $*" >&2; exit 1; }

[ -z "$(git status --porcelain)" ] || fail "working tree not clean"
[ "$(git branch --show-current)" = main ] || fail "release from main"
LAST=$(git tag --sort=-v:refname | head -1)
python3 -c "import sys; v=lambda s: tuple(map(int, s.lstrip('v').split('.'))); sys.exit(v('$T') <= v('${LAST:-v0.0.0}'))" \
  || fail "$T is not newer than $LAST"
grep -q "^## $V" CHANGELOG.md || fail "CHANGELOG.md has no '## $V' section"

for t in hooks/test_settle.py hooks/test_shell.py hooks/test_bundle.py hooks/test_forge.py; do
  python3 "$t" || fail "$t failed"
done
if [ -n "${LF55_REPLAY:-}" ]; then sh -c "$LF55_REPLAY" || fail "replay failed"; fi

git tag -a "$T" -m "lean-forge-55 $V"
git push origin main "$T"
awk -v v="## $V" 'index($0, v) == 1 {on=1; next} /^## / {on=0} on' CHANGELOG.md > "${TMPDIR:-/tmp}/lf55-notes.md"
gh release create "$T" --title "lean-forge-55 $V" --notes-file "${TMPDIR:-/tmp}/lf55-notes.md"

claude plugin marketplace update lean-forge-55
claude plugin update lean-forge-55@lean-forge-55

# Running sessions keep the plugin folder they started with (marked .in_use) until restarted: give those the tagged
# files too, one atomic replace per file, so a hook never reads half a file. ponytail: hooks.json changes (new or
# removed hooks) still need a restart; only file contents are refreshed here.
python3 - "$T" <<'PY'
import json, os, shutil, subprocess, sys, filecmp
tag = sys.argv[1]
cache = os.path.expanduser("~/.claude/plugins/cache/lean-forge-55/lean-forge-55")
files = subprocess.run(["git", "ls-files", "hooks", "scripts", "protocol.md", "protocol-55.md", "skills",
                        ".claude-plugin/plugin.json"],  # plugin.json too: the log names the release from it
                       capture_output=True, text=True, check=True).stdout.split()
dirs = [os.path.join(cache, d) for d in os.listdir(cache)]
for d in dirs:
    if not os.path.exists(os.path.join(d, ".in_use")) and not os.path.exists(os.path.join(d, "hooks")):
        continue
    for f in files:
        dst = os.path.join(d, f)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(f, dst + ".lf55new")
        os.replace(dst + ".lf55new", dst)
bad = [(d, f) for d in dirs for f in files if not filecmp.cmp(f, os.path.join(d, f), shallow=False)]
head = subprocess.run(["git", "rev-list", "-n1", tag], capture_output=True, text=True, check=True).stdout.strip()
inst = json.load(open(os.path.expanduser("~/.claude/plugins/installed_plugins.json")))["plugins"]["lean-forge-55@lean-forge-55"][0]
print(f"installed: {inst['installPath']} @ {inst.get('gitCommitSha', '?')[:7]} (tag {head[:7]}); refreshed {len(dirs)} folder(s)")
if bad or inst.get("gitCommitSha") != head:
    sys.exit(f"release: install does not match {tag}: {len(bad)} differing files, sha {inst.get('gitCommitSha')}")
print(f"release ok: {tag}")
PY
