# Security Audit

Automated and manual security review for codebases.

## Tools

- `exec_command` — run security scanners (bandit, npm audit, etc.)
- `read_file` — review source code for vulnerabilities
- `search_files` — find patterns of insecure code

## Workflow

### 1. Automated Scan

Run the appropriate scanner for the project's language:

**Python projects:**
```
pip install bandit && bandit -r . -f json -o bandit-report.json
```

**Node.js projects:**
```
npm audit --json > npm-audit.json
```

**Go projects:**
```
go vet ./...
gosec ./...
```

Parse the scanner output and categorise findings by severity (critical / high / medium / low).

### 2. Manual Review

Search for common vulnerability patterns:

#### Injection
- SQL injection: search for raw string concatenation in queries
- Command injection: search for `os.system`, `subprocess` with shell=True
- Path traversal: search for unsanitised file path inputs

#### Authentication & Authorization
- Hardcoded credentials: search for `password`, `secret`, `api_key` in source
- Missing auth checks: search for route handlers without auth middleware
- Insecure session management

#### Data Exposure
- Sensitive data in logs: search for log statements that dump user data
- Verbose error messages leaking internals
- Missing HTTPS enforcement

#### Dependencies
- Outdated packages with known CVEs
- Unused dependencies (attack surface)

### 3. Report Generation

Produce a structured report with:

```markdown
# Security Audit Report

**Date**: <today>
**Scope**: <files/directories scanned>
**Tool**: <scanner name + version>

## Summary
- Critical: N
- High: N
- Medium: N
- Low: N

## Findings

### [CRITICAL] Finding Title
- **File**: path/to/file.py:42
- **Type**: SQL Injection
- **Description**: ...
- **Remediation**: ...
- **CVSS**: N.N

...

## Recommendations
1. ...
```

## Guidelines

- Never exploit vulnerabilities — only confirm existence
- Prioritise findings by real-world exploitability, not just CVSS score
- Include remediation advice for every finding
- Flag false positives explicitly
- Re-scan after fixes to confirm resolution
