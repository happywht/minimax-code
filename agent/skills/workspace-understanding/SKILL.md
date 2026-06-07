---
name: workspace-understanding
version: 1.0.0
description: |
  Provides deep understanding of the workspace structure, tech stack,
  and code organization. Helps answer questions like "how is this
  project structured?" or "where is the X feature implemented?"
when_to_use: |
  Use this skill when the user asks about the project structure,
  wants to understand how different parts of the codebase connect,
  or needs a high-level overview of the architecture. Also useful
  when onboarding to a new codebase.
tools:
  - glob_find
  - read_file
  - search_files
---

# Workspace Understanding

This skill helps analyze and explain the structure of a codebase.

## Workflow

### 1. Scan

- Use `glob_find` to discover the top-level directory structure
- Identify the project type (language, framework, build tool) from
  config files (`package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`,
  `pom.xml`, `build.gradle`, etc.)
- Build a mental model of the directory conventions

### 2. Map

- Trace entry points (`main.py`, `index.ts`, `main.go`, `App.tsx`,
  `router.ts`, `server.py`, etc.)
- Identify the module/package structure and naming conventions
- Locate configuration, test, and documentation directories
- Search for import/dependency patterns to understand module relationships

### 3. Explain

Provide a structured overview using this format:

```markdown
# Workspace Overview

## Tech Stack
- **Language**: <primary language + version>
- **Framework**: <framework name + version>
- **Build Tool**: <build tool>
- **Package Manager**: <package manager>
- **Testing**: <test framework>

## Directory Layout

```
<top-level dirs with 2-line descriptions>
```

## Entry Points
- <path/to/entry> — <what it does>

## Key Patterns
- **Naming**: <conventions observed>
- **Architecture**: <patterns observed (MVC, microservices, monolith, etc.)>
- **State Management**: <if applicable>

## Major Dependencies
- `<package>` — <role in the project>
```

## Guidelines

- Start broad (directory structure) then go deep (entry points, patterns)
- Report what you *observe*, not what you assume
- If a config file is ambiguous, read it to resolve
- Keep the overview concise — the user can ask follow-up questions
- Always identify the primary language and framework first
