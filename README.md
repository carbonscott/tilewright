# tilewright

Minimal [Tiled](https://blueskyproject.io/tiled/) catalog broker. A dataset is
described once in a YAML contract; that contract compiles to Parquet manifests,
which register into a running Tiled server over HTTP. Datasets are keyed by
human-readable names, their physics parameters become queryable metadata, and
arrays are served lazily from the source HDF5 files.

## Requirements

- Python ≥ 3.11
- [`uv`](https://docs.astral.sh/uv/)

## Installation

```bash
# from the repo root
uv sync                     # creates .venv with tiled + deps
```

On S3DF, optionally point `UV_CACHE_DIR` at a shared cache first to avoid
re-downloads:

```bash
export UV_CACHE_DIR=/sdf/data/lcls/ds/prj/prjmaiqmag01/results/cwang31/.UV_CACHE
```

Run the CLI with `uv run tilewright <command>` (or `uv tool install .` for a bare
`tilewright`). There are two commands, each self-documenting via `--help`:

| command | does |
|---|---|
| `tilewright manifest`  | validate a dataset YAML and generate its Parquet manifests |
| `tilewright register`  | register manifests into a running Tiled server over HTTP |

## Registering a dataset

### 1. Onboard it — agentic, the first step

A new dataset's on-disk layout is **not** assumed. Run the **`tilewright-onboard`**
skill (`skills/tilewright-onboard/` — a Claude Code skill; point Claude at it, e.g.
symlink into `~/.claude/skills/`) on the dataset directory. The agent inspects the
data, chooses one source archetype, authors the dataset YAML, and generates the
Parquet manifest with the entity/artifact counts it predicted first — then **stops
before registration**. Its work passes two machine-checkable gates:

```bash
uv run tilewright manifest examples/datasets/<name>.yml --check                        # Gate A: contract OK
uv run tilewright manifest examples/datasets/<name>.yml -o examples/manifests/<NAME>    # Gate B: counts
```

The result is a validated `examples/datasets/<name>.yml` and
`examples/manifests/<NAME>/{entities,artifacts}.parquet`.

The YAML contract is a tagged union — `source: files | batch | table` — plus a
`key`, provenance `metadata`, and an `artifacts` list. See `examples/datasets/` for
worked examples and `skills/tilewright-onboard/reference/onboarding.md` for the full
field reference.

### 2. Serve

Start the Tiled server once. It reads `config.yml` and serves on `127.0.0.1:8017`:

```bash
uv run tiled serve config config.yml --api-key tcbmin
```

### 3. Register

```bash
uv run tilewright register examples/datasets/<name>.yml \
  --manifests examples/manifests/<NAME> \
  --url http://localhost:8017 --api-key tcbmin
```

The dataset is now in the catalog:

```python
from tiled.client import from_uri
from tiled.queries import Key

c = from_uri("http://localhost:8017", api_key="tcbmin")
list(c)                                     # dataset keys
c["<NAME>"].search(Key("<param>") > 0)      # query entities by metadata
```
