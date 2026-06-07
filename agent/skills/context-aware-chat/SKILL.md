---
name: context-aware-chat
version: 1.0.0
description: |
  Enables context-aware conversation by leveraging the repo-map
  index and conversation history compaction. This skill instructs
  the LLM to be mindful of context window usage, reference the
  repo-map for codebase awareness, and maintain coherence when
  older conversation turns are compacted.
when_to_use: |
  Use this skill when the user is having a long conversation that
  spans many turns, when they ask about project structure or
  cross-file relationships, or when the conversation approaches
  the context window limit and older messages are being summarized.
---

# Context-Aware Chat

This skill is always active behind the scenes. It does not register
any tools — it relies on the global tool set (`read_file`,
`search_files`, `find_files`, etc.) and the repo-map injected into
the system prompt.

## Context Discipline

1. **Check the repo-map first.** The compressed symbol tree at the
   top of the system prompt gives you the project layout in under
   2K tokens. Use it to locate files before reading them.

2. **Be concise.** When the context indicator shows >70% usage,
   prioritise the most relevant information. Quote only the lines
   you need. Avoid dumping entire files.

3. **Acknowledge compaction.** If the user references something
   from an earlier turn that you cannot see, say so honestly:
   "I don't have the full context from that earlier message —
   could you repeat the key detail?"

4. **Prefer targeted reads.** Use `read_file` with a line range
   instead of reading an entire file. The repo-map tells you the
   relevant line numbers.

## When Context Runs Low

- Summarise your current understanding before the compaction
  kicks in. This helps the next turn start from a good base.
- If the user asks about something you covered before compaction,
  use `search_files` or `read_file` to re-discover the answer
  rather than guessing.
- Never fabricate details you don't have. Data integrity matters
  more than appearing to remember everything.

## Anti-patterns

- **Don't re-read files you already have in context** unless the
  user explicitly asks you to refresh.
- **Don't parrot the entire repo-map** back to the user. They
  see the codebase every day — they need answers, not a file list.
- **Don't apologise excessively** for compaction. It's a normal
  part of long conversations. Just re-acquire what you need.
