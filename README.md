[![CI](https://github.com/vanandrew/difftrace/actions/workflows/ci.yml/badge.svg)](https://github.com/vanandrew/difftrace/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/vanandrew/difftrace/graph/badge.svg?token=OukcZItBZo)](https://codecov.io/gh/vanandrew/difftrace)
[![PyPI - Version](https://img.shields.io/pypi/v/difftrace?style=flat)](https://pypi.org/project/difftrace/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/difftrace?style=flat)](https://pypi.org/project/difftrace/)
[![PyPI - License](https://img.shields.io/pypi/l/difftrace?style=flat)](https://pypi.org/project/difftrace/)

# difftrace

Change detection for uv monorepos. Parses `uv.lock` to build the workspace dependency graph, maps `git diff` output to packages, and BFS-traverses reverse dependencies to find all transitively affected packages.

**Zero runtime dependencies** — stdlib only. Python 3.11+.

## Installation

```bash
pip install difftrace
```

Or with uv:

```bash
uv add difftrace --dev
```

## CLI Usage

```bash
# Show affected packages (human-readable)
difftrace --base origin/main

# JSON output for CI
difftrace --base origin/main --json

# Custom lock file path
difftrace --lock-file path/to/uv.lock

# Exclude dev/optional dependencies from graph
difftrace --no-dev --no-optional
```

### Output

Human-readable:
```
Affected packages (3):
  - shared (direct)
  - api (transitive)
  - worker (transitive)
```

JSON (`--json`):
```json
{
  "directly_changed": ["shared"],
  "affected": ["api", "shared", "worker"],
  "test_all": false
}
```

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--base` | `origin/main` | Base ref to diff against |
| `--json` | off | Output as JSON |
| `--lock-file` | `uv.lock` | Path to lock file |
| `--no-dev` | off | Exclude dev dependencies |
| `--no-optional` | off | Exclude optional dependencies |

## GitHub Action

```yaml
jobs:
  detect:
    runs-on: ubuntu-latest
    outputs:
      matrix: ${{ steps.diff.outputs.matrix }}
      has_affected: ${{ steps.diff.outputs.has_affected }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: vanandrew/difftrace@v1
        id: diff
        with:
          base: origin/main

  test:
    needs: detect
    if: needs.detect.outputs.has_affected == 'true'
    runs-on: ubuntu-latest
    strategy:
      matrix: ${{ fromJson(needs.detect.outputs.matrix) }}
    steps:
      - uses: actions/checkout@v4
      - run: echo "Testing ${{ matrix.package }}"
```

### Action Inputs

| Input | Default | Description |
|-------|---------|-------------|
| `base` | `origin/main` | Base ref to diff against |
| `lock-file` | `uv.lock` | Path to lock file |

### Action Outputs

| Output | Description |
|--------|-------------|
| `affected` | JSON array of affected package names |
| `matrix` | `{"package": [...]}` for `strategy.matrix` |
| `has_affected` | `"true"` or `"false"` |
| `test_all` | `"true"` if root config changed |

## How It Works

1. **Parse** `uv.lock` to extract workspace members and their dependencies
2. **Diff** `git diff --name-only base...HEAD` to get changed files
3. **Map** changed files to packages via source path prefix matching
4. **Traverse** reverse dependency graph (BFS) to find all transitively affected packages

### Edge Cases

- **Nested workspaces**: workspace root != git root — paths are normalized automatically
- **Virtual root packages**: skipped during file matching to avoid false positives
- **Root config changes**: `pyproject.toml`, `uv.lock`, `.github/*` trigger testing all packages
- **Cycles**: BFS visited set prevents infinite loops

## License

MIT
