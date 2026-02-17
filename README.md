[![CI](https://github.com/vanandrew/difftrace/actions/workflows/ci.yml/badge.svg)](https://github.com/vanandrew/difftrace/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/vanandrew/difftrace/graph/badge.svg?token=OukcZItBZo)](https://codecov.io/gh/vanandrew/difftrace)
[![PyPI - Version](https://img.shields.io/pypi/v/difftrace?style=flat)](https://pypi.org/project/difftrace/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/difftrace?style=flat)](https://pypi.org/project/difftrace/)
[![PyPI - License](https://img.shields.io/pypi/l/difftrace?style=flat)](https://pypi.org/project/difftrace/)

# difftrace

Change detection for [uv](https://docs.astral.sh/uv/) monorepos. Parses `uv.lock` to build the workspace dependency graph, maps `git diff` output to packages, and BFS-traverses reverse dependencies to find all transitively affected packages.

**Zero runtime dependencies** — stdlib only. Python 3.11+.

## Why?

In a monorepo with many packages, running every pipeline on every PR is slow and wasteful. difftrace figures out *which* packages are actually affected by a change — both directly (files changed inside the package) and transitively (a dependency of that package changed) — so your CI only builds, tests, lints, and deploys what matters.

```
packages/shared/lib.py changed
        │
        ▼
   ┌─────────┐
   │ shared  │  ← directly changed
   └─────────┘
    ▲        ▲
    │        │
┌──────┐ ┌────────┐
│  api │ │ worker │  ← transitively affected
└──────┘ └────────┘
```

## Installation

```bash
pip install difftrace
```

Or with uv:

```bash
uv add difftrace --dev
```

## How It Works

1. **Parse** `uv.lock` to extract workspace members and their inter-package dependencies (external packages are excluded)
2. **Diff** `git diff --name-only base...HEAD` to get changed files
3. **Map** changed files to packages via longest source-path prefix matching
4. **Traverse** the reverse dependency graph (BFS) to find all transitively affected packages

### Root Triggers

Certain files at the root of your workspace indicate a change that affects *all* packages. By default, changes to `pyproject.toml`, `uv.lock`, or anything under `.github/` will set `test_all: true`. You can add custom triggers with `--root-trigger`.

### Edge Cases

- **Nested workspaces** — workspace root != git root? Paths are normalized automatically
- **Virtual root packages** — skipped during file matching to avoid false positives (a virtual root at `.` would otherwise match every file)
- **Cycles** — BFS uses a visited set to prevent infinite loops
- **Longest prefix matching** — `packages/api-extra/foo.py` won't incorrectly match `packages/api`

## CLI Usage

```bash
# Show affected packages (human-readable)
difftrace --base origin/main

# JSON output for CI pipelines
difftrace --base origin/main --json

# Just the package names, one per line (useful for scripting)
difftrace --names

# Just the source paths, one per line
difftrace --paths

# Only directly changed packages (skip transitive dependents)
difftrace --direct-only

# Show which files mapped to which packages
difftrace --detailed

# Custom lock file path
difftrace --lock-file path/to/uv.lock

# Exclude dev/optional dependencies from the graph
difftrace --no-dev --no-optional

# Exclude specific packages from the output
difftrace --exclude docs --exclude examples

# Add custom root-level triggers
difftrace --root-trigger Dockerfile --root-trigger "config/"

# Debug logging
difftrace -v
```

### Output Formats

**Human-readable** (default):
```
Affected packages (3):
  - shared (direct)
  - api (transitive)
  - worker (transitive)
```

**Human-readable with `--detailed`**:
```
Changed files (2):
  packages/shared/lib.py -> shared
  README.md -> (root/unmatched)

Affected packages (3):
  - shared (direct)
  - api (transitive)
  - worker (transitive)
```

**JSON** (`--json`):
```json
{
  "directly_changed": ["shared"],
  "affected": ["api", "shared", "worker"],
  "test_all": false
}
```

**Names** (`--names`):
```
api
shared
worker
```

**Paths** (`--paths`):
```
packages/api
packages/shared
packages/worker
```

### All Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--base` | `origin/main` | Base git ref to diff against |
| `--lock-file` | `uv.lock` | Path to uv lock file |
| `--json` | off | Output as JSON |
| `--names` | off | Output affected package names, one per line |
| `--paths` | off | Output affected source paths, one per line |
| `--direct-only` | off | Only report directly changed packages |
| `--detailed` | off | Include file-to-package mappings in output |
| `--no-dev` | off | Exclude dev dependencies from the graph |
| `--no-optional` | off | Exclude optional dependencies from the graph |
| `--root-trigger` | — | Additional root-level trigger patterns (repeatable) |
| `--exclude` | — | Exclude a package from the affected set (repeatable) |
| `-v` / `--verbose` | off | Enable debug logging |

> `--json`, `--names`, and `--paths` are mutually exclusive. If none are specified, human-readable output is used.

## GitHub Action

difftrace ships as a composite GitHub Action so you can use it directly in your workflows. It handles Python setup, installation, and output parsing for you.

```yaml
jobs:
  detect:
    runs-on: ubuntu-latest
    outputs:
      matrix: ${{ steps.diff.outputs.matrix }}
      has_affected: ${{ steps.diff.outputs.has_affected }}
      test_all: ${{ steps.diff.outputs.test_all }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0  # required so git diff can see the full history
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
      fail-fast: false
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - name: Run pytest
        run: uv run --directory packages/${{ matrix.package }} pytest

  build:
    needs: [detect, test]
    if: needs.detect.outputs.has_affected == 'true'
    runs-on: ubuntu-latest
    strategy:
      matrix: ${{ fromJson(needs.detect.outputs.matrix) }}
    steps:
      - uses: actions/checkout@v4
      - name: Build image
        run: |
          docker build \
            -f packages/${{ matrix.package }}/Dockerfile \
            -t ${{ matrix.package }}:${{ github.sha }} .

  deploy:
    needs: [detect, build]
    if: github.ref == 'refs/heads/main' && needs.detect.outputs.has_affected == 'true'
    runs-on: ubuntu-latest
    strategy:
      matrix: ${{ fromJson(needs.detect.outputs.matrix) }}
    steps:
      - uses: actions/checkout@v4
      - name: Deploy ${{ matrix.package }}
        run: echo "Deploying ${{ matrix.package }}"
```

The `matrix.package` output works with any per-package step — tests, builds, linting, deploys, etc. The example above shows a typical pipeline where each stage gates the next: **detect** → **test** → **build** → **deploy**. The `build` job only runs for packages that pass tests, and `deploy` only runs on the `main` branch.

> **Note:** `fetch-depth: 0` is required on the checkout step so that `git diff` can compare against the base ref. Without it, the shallow clone won't have enough history and difftrace will fail.

### Action Inputs

| Input | Default | Description |
|-------|---------|-------------|
| `base` | `origin/main` | Base ref to diff against |
| `lock-file` | `uv.lock` | Path to uv lock file |
| `exclude-packages` | — | Comma-separated list of packages to exclude |
| `no-dev` | `false` | Exclude dev dependencies from the dependency graph |
| `no-optional` | `false` | Exclude optional dependencies from the dependency graph |
| `direct-only` | `false` | Only output directly changed packages, skip transitive dependents |
| `root-triggers` | — | Comma-separated list of additional trigger patterns (e.g. `Dockerfile,docker/`) |
| `verbose` | `false` | Enable debug logging to stderr |

### Action Outputs

| Output | Description |
|--------|-------------|
| `affected` | JSON array of affected package names |
| `matrix` | `{"package": [...]}` for `strategy.matrix` |
| `has_affected` | `"true"` or `"false"` |
| `test_all` | `"true"` if root config changed |

## License

MIT
