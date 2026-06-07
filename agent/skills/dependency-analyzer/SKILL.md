---
name: dependency-analyzer
version: 1.0.0
description: |
  Analyzes project dependencies for security issues, outdated versions,
  and dependency graph structure. Supports Python, Node.js, and Go projects.
when_to_use: |
  Use this skill when the user wants to analyze their project's
  dependencies, check for security vulnerabilities, find outdated
  packages, or understand the dependency graph. Triggers on phrases
  like "analyze dependencies", "check for vulnerable packages",
  "what depends on X", or "dependency tree".
tools:
  - exec_command
  - read_file
  - search_files
---

# Dependency Analyzer

This skill helps analyze and audit project dependencies.

## Workflow

### 1. Detect

Read the lockfile or manifest to identify the package manager:

| File | Ecosystem | Package Manager |
|------|-----------|-----------------|
| `package.json` | Node.js | npm / yarn / pnpm |
| `pnpm-lock.yaml` | Node.js | pnpm |
| `package-lock.json` | Node.js | npm |
| `yarn.lock` | Node.js | yarn |
| `requirements.txt` | Python | pip |
| `Pipfile.lock` | Python | pipenv |
| `pyproject.toml` | Python | poetry / hatch |
| `uv.lock` | Python | uv |
| `go.sum` | Go | go modules |
| `Cargo.lock` | Rust | cargo |

### 2. Audit

Run the appropriate audit command for the ecosystem:

**Node.js:**
```bash
npm audit --json 2>/dev/null || pnpm audit --json 2>/dev/null
```

**Python:**
```bash
pip audit --format json 2>/dev/null || safety check --json 2>/dev/null
```

If no audit tool is available, manually inspect `requirements.txt` or
`pyproject.toml` for known vulnerable versions.

**Go:**
```bash
go vet ./...
govulncheck ./... 2>/dev/null || nancy go.sum 2>/dev/null
```

### 3. Analyse

Beyond security, also check for:

- **Outdated packages**: Compare installed versions with latest
- **Unused dependencies**: Cross-reference imports vs declared deps
- **Duplicate functionality**: Multiple packages solving the same problem
- **License compliance**: Flag GPL / AGPL in non-GPL projects

### 4. Report

Summarize findings by severity:

```markdown
# Dependency Analysis Report

**Ecosystem**: <ecosystem>
**Package Manager**: <manager>
**Total Dependencies**: N direct, M transitive

## Security Vulnerabilities

| Severity | Package | Vulnerability | Fixed In |
|----------|---------|--------------|----------|
| CRITICAL | <pkg@ver> | CVE-XXXX-XXXX | >=X.Y.Z |
| HIGH     | <pkg@ver> | CVE-XXXX-XXXX | >=X.Y.Z |

## Outdated Packages

| Package | Current | Latest | Major? |
|---------|---------|--------|--------|
| <pkg>   | 1.2.3   | 2.0.0  | Yes    |

## Recommendations

1. `npm audit fix` / `pip install --upgrade <pkg>` to resolve vulnerabilities
2. Consider replacing <pkg> with <alternative> for <reason>
```

## Guidelines

- Always check for security issues first (highest priority)
- Prefer automated scanners over manual inspection when available
- Include fix commands the user can copy-paste
- Flag transitive dependencies (not just direct) that have vulnerabilities
- If a scan tool is not installed, suggest the install command and continue
  with manual analysis
