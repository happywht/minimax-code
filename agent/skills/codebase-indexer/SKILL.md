---
name: codebase-indexer
version: 1.0.0
description: |
  Provides codebase navigation using the repo-map index.
  The repo-map is a compressed symbol tree injected into the
  system prompt showing the structure of every source file in
  the workspace. This skill instructs the LLM how to read and
  interpret the repo-map for efficient codebase navigation.
when_to_use: |
  Use this skill when the user asks about project structure,
  where a function or class is defined, what modules exist,
  or how different parts of the codebase relate to each other.
  Also useful when planning changes that span multiple files.
---

# Codebase Indexer

The repo-map is automatically injected into the system prompt
under the "Session context" section. It provides a compressed
outline of every source file in the workspace:

```
repo-map (23 files, ~1.8k tokens)
agent/minimax_code/agent/core.py
  AgentConfig
  AgentCore
    run() -> AgentRunResult
    _stream_turn() -> LLMResponse
web/src/stores/chat.ts
  useChat (ChatState)
    send()
```

## Navigation Strategy

1. **Scan the repo-map first** — locate relevant modules, classes,
   and functions before reading any file. This gives you the big
   picture without consuming context on full file contents.

2. **Use `read_file` to inspect details** — once you've identified
   the relevant file from the repo-map, use `read_file` with a
   targeted line range to see the actual implementation.

3. **Use `find_files` for discovery** — if the repo-map doesn't
   mention a file you're looking for (it may be truncated), use
   `find_files` with a glob pattern to discover additional files.

4. **Use `search_files` for cross-references** — to find all usages
   of a symbol across the codebase, use `search_files` with the
   symbol name.

## Limitations

- The repo-map is **compressed**: deep nesting is truncated, and
  some files may be omitted to stay within the token budget.
- It is **regenerated on file changes** but may be stale by a few
  seconds during rapid editing. Always verify with `read_file`.
- It only covers **source files** (Python, TypeScript, JavaScript,
  Java, Go). Configuration files, docs, and assets are excluded.
