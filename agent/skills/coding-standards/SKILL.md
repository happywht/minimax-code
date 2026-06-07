---
name: coding-standards
version: 1.0.0
description: |
  Checks Python code against naming conventions, style rules,
  and docstring coverage. Combines ruff (when available) with
  AST-based heuristic checks for environments without ruff.
when_to_use: |
  Use this skill when the user asks to "check style", "check naming",
  "check docstrings", "verify coding conventions", or wants a
  general code quality report beyond what a linter covers.

  Do NOT use for non-Python files — all three tools are Python-specific.
tools:
  - check_style
  - check_naming
  - check_docstring
---

# Coding Standards

The skill runs three complementary checks against Python source
files. The agent's job is to *interpret* the merged findings and
produce a clear, actionable report.

## Workflow

1. Call `check_style(path, max_line_length=100)` to run the style
   checker. If ruff is installed it uses ruff; otherwise a built-in
   heuristic checks line length and trailing whitespace.
2. Call `check_naming(path)` to detect naming convention violations:
   - Classes should use PascalCase
   - Functions/methods should use snake_case
   - Module-level constants should use UPPER_SNAKE_CASE
3. Call `check_docstring(path, check_style=True)` to assess docstring
   coverage and validate docstring format (Google / NumPy / Sphinx).
   The tool reports the overall coverage percentage.
4. Summarise all findings grouped by severity:
   - **Warnings** — missing docstrings, naming violations
   - **Info** — unrecognised docstring styles
5. Produce a per-file summary:
   ```
   ## file.py
   - Style: 3 findings (2 line-length, 1 trailing whitespace)
   - Naming: 1 violation (class `my_class` → `MyClass`)
   - Docstrings: 75% coverage (2 missing)
   ```

## Naming Conventions

| Element | Convention | Example |
|---------|-----------|---------|
| Class | PascalCase | `MyClass`, `HTTPRequest` |
| Function | snake_case | `process_data`, `get_user` |
| Method | snake_case | `calculate_total` |
| Constant | UPPER_SNAKE | `MAX_RETRIES`, `DEFAULT_TIMEOUT` |
| Variable | snake_case | `user_count`, `is_active` |
| Private | _prefix | `_internal_cache` |

## Docstring Styles

The tool recognises three common Python docstring styles:

- **Google style**: Uses `Args:`, `Returns:`, `Raises:` sections
- **NumPy style**: Uses underlined section headers (`Parameters\n----------`)
- **Sphinx style**: Uses `:param`, `:type`, `:returns:` directives

Short single-line docstrings are always accepted.

## Limitations

- Style checking without ruff only covers line length and trailing
  whitespace — not the full PEP 8 rule set.
- Naming checks use AST analysis and may produce false positives
  for intentionally unconventional names (e.g., `HTTPServer`).
- Docstring coverage only counts public (non-underscore) symbols.
