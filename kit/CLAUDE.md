@AGENTS.md

## Claude Code

- Skills live in `.claude/skills/`. In a terminal session run one with `/name`, for example
  `/onboard voice`. In T3 Code, type `$` to pick one. Plain requests work too.
- A safety hook (`.kit/bin/brain-guard`) checks commands, file reads and edits. If it blocks
  something, tell the user in one line what was blocked and why, then offer the safe path: a
  draft in chat (or a Gmail draft with `brain draft email`, once Google is connected), or the
  change in their own files.
- Never retry a blocked action another way (a full path, `sh -c`, encoded text).
- The user's own Claude settings go in `.claude/settings.local.json`, never `.claude/settings.json`.
