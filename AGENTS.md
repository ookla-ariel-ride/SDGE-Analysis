# Agent entrypoint

Read [CLAUDE.md](CLAUDE.md) completely before work in this repository.
It is the shared canonical project guidance for every agent, regardless of tool.
Current user instructions take precedence. This entrypoint does not replace that guidance.

1. Keep the task within its stated scope. Give each subagent exact files and acceptance criteria.
   Use isolated worktrees with their own environment and staged private data, as CLAUDE.md §8 directs.
   Subagents leave changes uncommitted. The parent checks their work independently.
2. Keep raw household data in gitignored `private/` and secrets in gitignored `.env`.
   Follow the privacy gates in CLAUDE.md §4. Never remove `.env` from any checkout or worktree.
3. The parent commits and pushes small logical milestones after independent checks, frequently as work progresses.
   Follow the existing user authorization. A branch push does not authorize publication or merge.
   Run all publication gates before a report ships or a merge occurs, as CLAUDE.md §9 directs.
4. Close an issue only when evidence addresses every acceptance criterion.
   State any unmet criterion or limit before claiming completion.

Find setup and check commands at the top of CLAUDE.md.
