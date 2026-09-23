---
name: undo
description: Undo the file changes that shell commands made during the last turn (lean-forge-55). Use when the user asks to revert, roll back or undo what was just changed and /rewind cannot restore it because the edits were made through Bash.
---

# lean-forge-55 undo

Claude Code's `/rewind` does not restore files changed by Bash commands. lean-forge-55 archives the working tree
before the first shell command of each turn.

1. Find the undo command in the `<lean_forge_55>` block injected at session start (it carries the plugin path and
   this session's id). It has the form:
   `python3 "<plugin root>/scripts/lf55_snapshot.py" undo --session <session id>`
2. Tell the user which turn it restores (`... list --session <id>` shows snapshot times) and run it.
3. Report the JSON result: files restored, files created during the turn that were moved aside (not deleted), and
   the aside folder that holds everything the undo replaced, so the undo itself can be reversed.
