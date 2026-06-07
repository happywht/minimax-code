---
name: refactor-assistant
version: 1.0.0
description: |
  Assists with code refactoring using AST-aware rename and
  extract-function tools. Both tools default to dry_run=True so
  the user can preview changes before applying them.
when_to_use: |
  Use this skill when the user asks to rename a symbol across
  the codebase, extract a block of code into a new function,
  or perform other mechanical refactoring tasks. The skill
  provides AST-level accuracy for Python files and regex-level
  for other languages.
tools:
  - ast_rename
  - extract_function
---

# Refactor Assistant

This skill provides two precision refactoring tools that go beyond
simple text search-and-replace.

## Tools

### `ast_rename`

Renames a symbol (variable, function, class, method) across one or
more Python files using AST analysis for accuracy.

**Parameters:**
- `symbol` (required): The current name to rename.
- `new_name` (required): The replacement name.
- `paths` (optional): File or directory to scope the rename.
  Defaults to the workspace root.
- `dry_run` (optional, default `true`): When true, returns a
  preview of changes without modifying files.
- `max_files` (optional, default `100`): Safety cap on the number
  of files to scan.

**Behaviour:**
- For `.py` files: Uses `ast` module to distinguish symbols by
  context (e.g. `Foo.bar` the method vs `bar` the local variable).
- For non-Python files: Falls back to whole-word regex matching.
- Always respects `.gitignore` and skips binary files.

### `extract_function`

Extracts a range of lines from a file into a new function, replacing
the original lines with a call to the new function.

**Parameters:**
- `file_path` (required): The file to refactor.
- `start_line` (required): First line to extract (1-based).
- `end_line` (required): Last line to extract (1-based, inclusive).
- `function_name` (required): Name for the new function.
- `dry_run` (optional, default `true`): Preview only.
- `insert_before` (optional, default `false`): Insert the new
  function *before* the caller instead of after.

**Behaviour:**
- Infers parameters by analysing which variables from the outer
  scope are read inside the extracted range.
- Infers the return value by checking which variables are used
  after the extraction point.
- For Python files, attempts to maintain correct indentation.
- Places the new function immediately before/after the caller.

## Workflow

1. **Preview first.** Both tools default to `dry_run=True`. Show
   the user the preview and confirm before applying.
2. **Verify scope.** Before renaming, use `search_files` to check
   how many files reference the symbol. Warn the user if the rename
   touches >20 files.
3. **Test after refactoring.** Suggest running the test suite
   after applying changes. If the user has a `test_generator`
   skill available, offer to generate tests for the new function.
4. **Backup.** The system automatically backs up files before
   edits. Remind the user they can check `.minimax/backups/`
   if something goes wrong.

## Limitations

- `ast_rename` is Python-AST-aware only. For TypeScript, Java, Go,
  etc., it falls back to regex matching which is less precise.
- `extract_function` parameter inference is heuristic-based. Complex
  cases (closures, generators, exception handlers) may need manual
  adjustment.
- Neither tool handles cross-file type inference. For type-safe
  renames in TypeScript, the user should use their IDE's refactoring
  support.
