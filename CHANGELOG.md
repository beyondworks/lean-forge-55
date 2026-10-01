# Changelog

## 0.2.9 — 2026-10-01

- Codex support for a measurement week: `LF55_LOG_ONLY=1` makes forge.py decide and log (`would: deny/note`, model)
  without denying, noting or copying the tree; `apply_patch` is an edit tool whose patch headers decide scratch paths;
  Codex's `last_assistant_message` is the agent's last message; the model comes from the rollout's `turn_context`.

## 0.2.8 — 2026-10-01

- Undo copies are kept 7 days: at session start, other sessions' snapshot folders whose newest file is older than
  that are removed (the current session's folder stays). 252 MB had piled up in nine days.

## 0.2.7 — 2026-10-01

- Advisory-only guardian text (the commit "risk advisory", and confirm-level notices in auto/bypass modes) comes once per
  session and reason instead of on every command; denials and real permission prompts still come every time. Castra's
  "deferred/blocked remain" note repeats only when the count grows. Per-step harness text that changes nothing is what
  Claude Sonnet 5.5 may read as an injection attempt.

## 0.2.6 — 2026-10-01

- `log.jsonl` names the installed release (read from plugin.json); 0.2.5 still wrote 0.2.4.

## 0.2.5 — 2026-10-01

- Castra Stop blocks only for code this turn's own agent changed and that still exists. Files from earlier turns and
  from subagents (now marked with the hook's `agent_id`) are reported once without blocking. Scratchpad scripts stay
  tracked: the Stop block that found a timed-out analysis script (a real case) still fires. Transcripts, memory, plans,
  installed plugin copies, `~/.claude.json`, `~/.cache` and `SESSION_HANDOVER.md` are not tracked; hooks you write
  under `~/.claude` are.
- Castra: deferred/blocked keep their reason when something else changes the file; a check that passes on a deleted
  file closes it as removed; `verify --pending`, `--files-from -`, timeouts up to 1800 s; `defer/block --note`;
  `CLAUDE_CODE_SESSION_ID` names the session; `env-names <file>` lists variable names, never values.
- Guardian, .env: output that cannot carry a value passes (`grep -c/-q/-l`, `grep -o '^[A-Z_]+='`, `cut -s -d= -f1`,
  `.env.example/.sample/.template`). Forms that print lines without `=` (a multi-line key) stay denied, and `sed`,
  `awk`, `cut -f2`, `sort`, `strings`, `xxd`, `base64` and `readFileSync` on a .env are now denied too. Of 89 real
  guardian denials, 18 change to pass, all name-, count- or template-only.
- Denials say what was caught and the one way to continue, in Korean when the user writes Korean; Castra's Stop names
  the files.
- Protocol: the closing "any other rule?" only on a new feature's first SETTLE; values judged by eye are built and
  shown; "I'll proceed" means proceed in that turn. Rule 2: commits, pushes and PRs on a feature branch or worktree are
  part of an approved plan; merging into main, production deploys, releases, `branch -D`, `worktree remove --force`
  and force pushes need the user to name them.

## 0.2.4 — 2026-10-01

Replayed on the author's 194 sessions since v0.2 (same history, same cached Jev answers): messages after which writes
were closed 137 → 94 of 2,388; blocked tool calls 30 → 12. Of the 21 blocks that went away, 14 were drafts, notes or
plans, 2 were commits or new branches, 5 followed a reply or arrived while the agent was working.

- SETTLE: a reply to a turn that ended asking opens writes unless Jev judges it a new task (`new_task`); answering
  inside `AskUserQuestion` opens; "show me a draft first" opens.
- SETTLE: a message that arrives mid-turn (read from the transcript, so turns started by a task notice or continued
  after a Stop-hook block count) keeps open work open; messages from other sessions and task notices change nothing.
- SETTLE: drafts and notes stay writable while closed (temp folders and the scratchpad, `~/.claude/plans/`, memory
  folders, `SESSION_HANDOVER.md` at the repo root, plus `scratch_paths` in the optional `config.json`); slash commands
  listed in `open_commands` open.
- Shell gate: `NAME=value` variables are followed into `cd` and paths; `git commit/merge/rebase/cherry-pick` and
  `checkout -b` are no longer gated; `git -C <dir>` no longer hides the subcommand.
- State: atomic replace; the fallback decision is saved before Jev is asked (a killed hook left an empty state file
  and the next hook fell back to "closed"); Jev unavailable keeps open work open; Jev timeout 3 s.
- The agent's last message for Jev is the latest block that asks, not just the last block.
- `log.jsonl`: one line per hook event (no message text, 30-day retention).
- Removed the per-prompt context-capacity reminder (castra-budget) from the hooks.
- `LF55_STATE_DIR` and `LF55_CONFIG` for tests; `scripts/release.sh` (tests → tag → release → plugin update →
  refresh in-use copies → verify the installed files match the tag).
