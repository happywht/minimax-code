---
name: code-review
version: 1.0.0
description: |
  Reviews Python code for correctness, style, and complexity.
  Combines a real linter (ruff → flake8 → pyflakes) with a
  custom cyclomatic-complexity analyser.
when_to_use: |
  Use this skill when the user asks the agent to "review" a
  file or directory, flag risky code, or produce a structured
  critique of a change. The skill surfaces concrete findings
  with file/line/severity, not a vague "looks fine".

  Do NOT use for non-Python files — both tools are
  Python-specific.
tools:
  - run_linter
  - find_complex_functions
---

# Code Review

The skill is intentionally mechanical: it runs two tools
back-to-back and reports the merged findings. The agent's
job is to *interpret* the findings, group related issues,
and write a useful summary for the user.

## Workflow

1. Call `find_complex_functions(path, threshold=10)` first.
   The complexity report is small (~1 finding per over-complex
   function) and grounds the rest of the review.
2. Call `run_linter(path)` next, scoped to the same path.
   The linter handles style (PEP 8, unused imports, undefined
   names). If neither `ruff` nor `flake8` nor `pyflakes` is
   installed, the tool returns an error — surface that to the
   user immediately; do not invent findings.
3. Read the source files the findings point at (use
   `read_file` from the global tool set) before suggesting
   fixes. A linter line number is a *clue*, not a verdict.
4. Group findings:
   - **Must fix**     — bugs, undefined names, syntax errors.
   - **Should fix**   — high complexity (>15), unused imports,
     missing type hints on public APIs.
   - **Nice to fix**  — style, line length, naming.
5. Write a per-file summary at the end:
   ```
   ## file.py
   - 2 must-fix issues (line 12, line 47)
   - 1 should-fix issue (line 33, complexity 14)
   - 4 nice-to-fix style nits
   ```

## Severity conventions

- `severity=error` in the linter output → **must-fix**.
- `severity=warning` from the linter, or complexity > 15 →
  **should-fix**.
- `severity=warning` with `code` starting with `W` or
  style-only → **nice-to-fix**.

## Limitations

- The complexity counter is *only* a static AST count; it
  does not measure cognitive load, test coverage, or
  runtime behaviour. A function with complexity 12 may still
  be perfectly readable.
- The linter is not a security scanner. For security
  concerns, the user should run `bandit` or `pip-audit`
  separately.
- The linter output is truncated at 500 findings by default.
  For very large PRs, ask the user which subdirectory to
  scope the review to.

## Anti-patterns to flag

When you see any of the following, surface it even if the
linter is silent:

- Bare `except:` clauses
- `try: ... except: pass` (silent failure)
- `from module import *`
- `print(...)` left in non-CLI code
- `assert` used for runtime validation (it disappears under
  `python -O`)
- `global` keyword in a function
- Mutable default arguments (`def f(items=[]): ...`)
