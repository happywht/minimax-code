---
name: smart-debug
version: 1.0.0
description: |
  Intelligent debugging assistant that systematically diagnoses
  and fixes bugs through reproduction, analysis, and targeted fixes.
when_to_use: |
  Use when the user reports a bug, asks for help debugging, or
  wants to trace a failure. Triggers on "debug this", "fix this error",
  "why does this crash", "something is broken".
tools:
  - exec_command
  - read_file
  - search_files
  - edit_file
---

# Smart Debug

Intelligent debugging assistant that systematically diagnoses and fixes bugs.

## Tools

- `exec_command` — run tests, inspect processes, check logs
- `read_file` — read source files, stack traces, config files
- `search_files` — find error patterns, trace function calls
- `edit_file` — apply fixes

## Workflow

### 1. Reproduce

- Ask the user for the error symptom or observe it from test output
- Run the failing test or command to confirm reproduction
- Record the exact error message, stack trace, and exit code

### 2. Collect

- Read the failing source file and related modules
- Search for similar error patterns in the codebase
- Check recent git changes that might have introduced the bug:
  ```
  git log --oneline -10
  git diff HEAD~1
  ```
- Review configuration files and environment settings

### 3. Hypothesise

- Based on the evidence, form 1–3 hypotheses ranked by likelihood
- For each hypothesis, identify the specific code path that would cause it
- State assumptions clearly before testing

### 4. Verify

- For the top hypothesis, add a targeted diagnostic:
  - Insert temporary logging or print statements
  - Run with verbose flags
  - Check variable values at the suspected failure point
- If the hypothesis is confirmed, proceed to Fix
- If disproven, move to the next hypothesis

### 5. Fix

- Make the minimal change that resolves the root cause
- Avoid band-aid fixes — address why the bug was possible
- Add a regression test that would have caught the original bug
- Run the full test suite for the affected module

### 6. Confirm

- Re-run the original failing test → must pass
- Run the new regression test → must pass
- Check that no other tests are broken
- Summarise: root cause, fix applied, tests added

## Guidelines

- Always reproduce before hypothesising
- Prefer reading over writing — understand before changing
- One hypothesis at a time, tested incrementally
- Minimal diffs — don't refactor while debugging
- When stuck, ask the user for context rather than guessing
