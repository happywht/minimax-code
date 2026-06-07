---
name: doc-generator
version: 1.0.0
description: |
  Extracts API signatures from Python source files and generates
  bilingual (English + Chinese) Markdown documentation.
when_to_use: |
  Use this skill when the user asks to "generate docs", "document
  the API", "create API reference", or "extract signatures".
  Works best on well-structured Python packages with docstrings.

  Do NOT use for non-Python files — both tools parse Python AST.
tools:
  - extract_api_signatures
  - generate_doc
---

# Doc Generator

The skill extracts structured API data from Python source files
and generates bilingual Markdown documentation. The agent's job
is to run both tools in sequence and deliver a complete document.

## Workflow

1. Call `extract_api_signatures(path, include_private=False)` to
   scan the target directory or file. The tool returns a list of
   signature objects, each containing:
   - `kind` — "function" or "class"
   - `name` — symbol name
   - `args` — formatted argument string
   - `returns` — return type annotation
   - `docstring` — extracted docstring
   - For classes: `bases`, `methods` (list of method signatures)
2. Call `generate_doc(signatures=<result>, title="API Reference",
   language="bilingual")` to produce the Markdown document.
   Language options:
   - `"bilingual"` — English + Chinese in one document
   - `"en"` — English only
   - `"zh"` — Chinese only
3. Review the generated document for completeness. If any public
   symbols lack docstrings, note it in the output and suggest
   the user add docstrings before re-running.

## Output Format

The generated document includes:

- **Title + generation date**
- **Table of Contents** — linked to each symbol
- **Per-symbol entries**:
  - Signature in a code block
  - Docstring (first line as summary)
  - For classes: list of methods with signatures

Bilingual mode produces two sections separated by a horizontal rule,
with the English section first.

## Example

Input signatures:
```json
[
  {
    "kind": "function",
    "name": "process_data",
    "args": "data: list[Any], timeout: int = 30",
    "returns": "list[Any]",
    "docstring": "Process data items with timeout."
  }
]
```

Output (English):
```markdown
## process_data

**Kind:** `function`

\```python
def process_data(data: list[Any], timeout: int = 30) -> `list[Any]`
\```

> Process data items with timeout.
```

## Limitations

- Only Python source files are supported.
- Type annotations are extracted via `ast.unparse` (Python 3.9+);
  on older Python, annotations may show as `...`.
- The tool does not follow imports or resolve cross-module
  references — it documents what is in the target path only.
