---
name: commit-helper
version: 1.0.0
description: |
  Helps craft well-formed git commit messages following
  Conventional Commits. Invoked when the user asks the agent
  to "commit this", "write a commit message", or wants the
  diff summarised.
when_to_use: |
  Use this skill when the user wants a commit message written
  for the current (or staged) changes. The skill invokes the
  `get_git_diff` tool to read the diff, then drafts a message
  in the Conventional Commits format
  (`<type>(<scope>)<!>: <subject>`).

  Do NOT use this skill for tasks unrelated to commits — the
  underlying tool runs `git diff` and the result is irrelevant
  when the user is asking about something else.
tools:
  - get_git_diff
---

# Commit Helper

This skill helps the agent draft a commit message that
follows [Conventional Commits
1.0.0](https://www.conventionalcommits.org/en/v1.0.0/).

## Workflow

1. Call `get_git_diff()` (default: unstaged) to read the
   pending changes.
2. If the diff is empty, ask the user whether they want a
   staged-only diff (`staged: true`) — sometimes `git status`
   shows nothing because everything is already staged.
3. Categorise the changes by the Conventional Commits
   taxonomy:
   - `feat`     — a new user-visible feature
   - `fix`      — a bug fix
   - `docs`     — documentation only
   - `style`    — formatting / whitespace, no code change
   - `refactor` — code change that neither fixes a bug nor adds a feature
   - `perf`     — performance improvement
   - `test`     — adding or fixing tests
   - `build`    — build system or external dependencies
   - `ci`       — CI configuration
   - `chore`    — other changes that don't modify `src` or `test`
4. Compose the message:

   ```
   <type>(<optional-scope>)<!>: <subject, max 72 chars, imperative>

   <body — wrap at 72 columns; explain *what* and *why*, not *how*>

   <footer — `BREAKING CHANGE: ...` for `!` types, or `Refs: ISSUE-123`>
   ```

5. **Always show the message to the user before running
   `git commit`**. The skill *generates* a message; the user
   must approve it. Never commit on the user's behalf without
   explicit confirmation.

## Examples

For a bug fix in the `parser` module:

```
fix(parser): reject empty document bodies

Previously `parse("")` returned `None`; downstream code then
crashed with `AttributeError`. The parser now raises
`ValueError` so the failure is caught at the right layer.

Refs: ISSUE-42
```

For a breaking API change:

```
feat(api)!: replace /v1/sessions with /v2/sessions

The new endpoint returns a paginated `SessionPage` object
instead of a flat array. Clients must update their pagination
logic; the old endpoint will return 410 Gone after 2026-09-01.

BREAKING CHANGE: `/v1/sessions` removed; use `/v2/sessions`.
```

## Edge cases

- **Merge commits.** Do not rewrite the message of an
  existing merge commit. Suggest `chore: merge main into
  feature/x` only if the user explicitly asks.
- **Reverts.** Use the `revert:` prefix followed by the SHA
  and the original subject, e.g.
  `revert: feat(api): replace /v1/sessions with /v2/sessions`.
- **Multi-purpose diffs.** When a single diff contains
  several logically distinct changes, prefer one commit per
  concern and tell the user to stage selectively.
