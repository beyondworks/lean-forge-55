# Changelog

## 0.2.17 — 2026-10-10

- SETTLE asks only after a denial (approved 2026-10-09): no denial means edits are open, even for a new feature; a
  design, UX or implementation choice inside approved work is not taste or policy; once the user says to continue,
  every reversible choice is settled for the session. This change had lived only in installed copies, so the 0.2.15
  and 0.2.16 releases overwrote it twice; it is now on main.

## 0.2.16 — 2026-10-10

- Security review of 0.2.15, both found and fixed the same day:
  - A subagent that started while the gate was open could take new work from the parent after the gate closed. Now
    a message the parent sends while closed (SendMessage, newly hooked) puts every remembered subagent back on the
    parent's gate. Restart a session to pick up the new hook.
  - The guardian's `process.env` exception looked at the whole argument, so `cat x/process.env/../.env` read a real
    `.env`. It now applies only when the name itself reads like code text (`process.env.API_URL`), inside an
    expression such as `s/…/…/`.

## 0.2.15 — 2026-10-10

- Removed what ten days of measurement (10/01–10/10, the author's Opus 5.5 sessions) showed did not help: Castra's
  Stop check and evidence ledger (23 blocks, 1 defect found), the Castra posture text and the Ponytail prompt
  (~22k characters a session, no consistent change in AI-written diffs). Their hooks, skills and scripts are gone;
  protocol.md keeps SETTLE and the `ponytail:` comment for a shortcut with a known ceiling.
- A subagent keeps the gate it started under: delegated work started while open finishes after the user's next message
  closes the parent's gate (four Argo subagents on approved PRs were denied mid-task); one started while closed stays closed.
- Guardian: `sed 's/process.env.A/process.env.B/'` and other edits of code that says `process.env` / `import.meta.env`
  are no longer read as opening a `.env` file (9 of 40 denials).

## 0.2.14 — 2026-10-08

- Ponytail is scoped to code. A written deliverable the user asked for (plan, proposal, spec, copy, script, email,
  document, brainstorm) is written at the length its purpose needs, with its options and ideas; the ladder, YAGNI and
  the "three short lines" output rule no longer shorten it, and a hypothesis in it is labeled rather than cut. The
  REPORT step's "a few lines" is the report on the work, not a requested document. Plans and copy were coming out
  short and flat because code rules were applied to prose.

## 0.2.13 — 2026-10-07

- Shell gate (third security review): every bash write operator is read — `N>`, `N>>`, `&>`, `&>>`, `>|`, `>&file` —
  not only `>`/`>>`; descriptor copies (`2>&1`, `>&2`) and arrows (`=>`, `->`) are not writes. Text is read as bash
  reads a command line, here-document bodies included, so HTML tag text like `<b>10/07` in a body can read as a write
  while the gate is closed. Accepted: separating data from commands needs a full shell parser, and since 0.2.10 document
  requests keep the gate open.

## 0.2.12 — 2026-10-07

- Shell gate (second security review): here-document parsing is gone; any line-based parser differs from the shell
  somewhere (`cat <<EOF | sh`, `eval "$(cat <<EOF`, a quote spanning lines all hid writes in 0.2.11). Bodies are read
  in full again, as in 0.2.9. The 0.2.10 false positive is fixed where it came from: a redirect target ends at `<` or
  `>`, as bash reads it, so HTML like `> </head>` has no target and `>app/a<b` still writes app/a.

## 0.2.11 — 2026-10-07

- Shell gate (security review of 0.2.10): only a here-document body that `cat`/`tee` writes out is treated as data,
  and a `<<` inside quotes no longer opens one. 0.2.10 let a quoted `'<<X'` hide the writes after it and skipped
  bodies fed to python. Both are caught again; the HTML wireframe case from 0.2.10 still passes.

## 0.2.10 — 2026-10-07

Fewer questions. Since 0.2.4 the gate closed after 31 of 535 messages, yet 140 of the 431 open turns still ended with
questions: the protocol text, not the gate, was asking.
- Protocol: while edits are open, settle choices yourself (your recommendation), build, and list the choices made in
  one or two lines of the report; do not stop to ask or end a finished report with a question. Ask first only after a
  denied write or before what cannot be undone. Opus 5.5 rule 1 reads the same way: contracts are still settled and
  named, never hidden.
- SETTLE: writing a document for the user to review (plan, proposal, report, brief, draft, mockup, wireframe) keeps
  edits open; open choices go inside the document. A real "write a plan from this meeting" request scored 0.86 → 0.44;
  the held-out must-ask set stays closed (0/5 opened).
- Shell gate: here-document bodies that are data are not read as commands. An HTML wireframe written outside the
  project was denied because "> </head>" in the HTML looked like a redirect. Code fed to python/node/sh is still read.

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
