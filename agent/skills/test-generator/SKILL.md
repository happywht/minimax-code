---
name: test-generator
version: 1.0.0
description: |
  Generates pytest unit tests for a Python function or module.
  Combines AST-based introspection (function signatures,
  dependencies) with the agent's own reasoning to write
  idiomatic tests.
when_to_use: |
  Use this skill when the user asks the agent to "write tests
  for X", "add pytest coverage for module Y", or "generate a
  test scaffold". The skill is Python-specific; for other
  languages, fall back to the general `write_file` tool.

  Do NOT use this skill to *run* tests — that's a regular
  `exec_command` with `pytest`.
tools:
  - extract_functions
  - analyze_function_dependencies
---

# Test Generator

The skill writes pytest tests for Python code. It does
**not** run the tests — it produces a `.py` file the user
can review and run themselves.

## Workflow

1. Call `extract_functions(path)` to get the list of
   top-level functions and methods. Each entry has
   `name`, `args`, `returns`, `docstring`, and `source`.
2. For each function the user wants covered, call
   `analyze_function_dependencies(path, func_name)` to find
   the free symbols and the names of other module-level
   functions it calls. Use this to decide which symbols to
   monkey-patch with `mocker.patch(...)` or `monkeypatch.setattr(...)`.
3. Read the function's source (already returned by
   `analyze_function_dependencies`) and identify branches:
   - `if` / `elif` / `else`
   - `for` / `while` loops
   - `try` / `except` blocks
   - early `return` paths
4. For each branch, write at least one test that exercises
   it. The minimum coverage for a "this is tested" claim is
   *one happy-path test* and *one error-path test* per
   public function.

## Scaffolding rules

- **File location.** Place the new test file at
  `tests/test_<module>.py` next to the project root, mirroring
  the source's import path:
  - source: `src/foo/bar.py` → tests: `tests/foo/test_bar.py`
  - source: `foo/bar.py`      → tests: `tests/test_bar.py`
- **Imports.** Use absolute imports relative to the source's
  package. Do *not* add `sys.path` manipulation — that's a code
  smell and confuses the test runner.
- **Naming.** Test functions must start with `test_`. Use the
  pattern `test_<func>_<scenario>_<expected>`:
  ```python
  def test_parse_empty_string_raises_value_error():
      ...
  ```
- **Fixtures.** If the function depends on a resource
  (filesystem, network, database), define a `pytest` fixture
  with a tight scope. Do *not* use `setUp` / `tearDown`
  methods — pytest fixtures are the modern convention.
- **Mocking.** Prefer `pytest-mock`'s `mocker` fixture over
  `unittest.mock.patch` (it's cleaner and auto-undoes patches
  on test teardown). For functions the analysis tool flagged
  as free symbols, mock them — never let a unit test reach
  into a real network or database.

## What to skip

- **Trivial getters / setters.** A `def get_x(self): return
  self._x` does not need a test.
- **`__repr__`, `__str__`.** Useful to test if the output is
  part of the public contract (e.g. dataclasses); otherwise
  skip.
- **Re-exports.** If a module just re-exports names from
  another module, test the source, not the re-export.

## Edge cases the agent should ask the user about

- **Floating-point comparisons.** Use `pytest.approx` instead
  of `==` for floats; ask the user if they need a specific
  tolerance.
- **Time / randomness.** Mock `time.time`, `random.random`,
  and `datetime.now` — don't test the wall clock.
- **Async code.** Use `pytest-asyncio`'s `@pytest.mark.asyncio`
  decorator; the user must have `pytest-asyncio` installed.
