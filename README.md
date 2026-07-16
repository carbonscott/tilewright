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

Run the CLI from your data root with `uv run --project <tilewright repo root>
tilewright <command>` (or `uv tool install .` for a bare `tilewright`).
`--project` selects the environment without changing the working directory.
There are two commands, each self-documenting via `--help`:

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
before registration**.

Its outputs land in a `.tilewright/` directory **inside the data root**, so the
manifest travels with the data it describes. `<KEY>` is the YAML's own `key:`
value; the file and the manifest directory are both named after it. Its work
passes two machine-checkable gates:

```bash
cd <data root>            # the directory holding your data, not the code tree
uv run --project <tilewright repo root> tilewright manifest .tilewright/datasets/<KEY>.yml --check                     # Gate A: contract OK
uv run --project <tilewright repo root> tilewright manifest .tilewright/datasets/<KEY>.yml -o .tilewright/manifests/<KEY>  # Gate B: counts
```

The result is a validated `.tilewright/datasets/<KEY>.yml` and
`.tilewright/manifests/<KEY>/{entities,artifacts}.parquet`.

The YAML contract is a tagged union — `source: files | batch | table` — plus a
`key`, provenance `metadata`, and an `artifacts` list. See `examples/datasets/` for
worked examples and `skills/tilewright-onboard/reference/onboarding.md` for the full
field reference.

### 2. Serve and register — the `tilewright-register` skill

Run the **`tilewright-register`** skill (`skills/tilewright-register/`). It writes
`.tilewright/config.yml`, starts the server, registers the manifests, and reads one
array back through HTTP to prove the bytes actually flow:

```bash
cd <data root>
uv run --project <tilewright repo root> tiled serve config .tilewright/config.yml --api-key tcbmin   # own terminal
uv run --project <tilewright repo root> tilewright register .tilewright/datasets/<KEY>.yml \
  --manifests .tilewright/manifests/<KEY> \
  --url http://localhost:8017 --api-key tcbmin
```

Because that config allowlists the data root — `.tilewright/`'s own parent — every
dataset onboarded under it is servable with **no config edit and no restart**. The
layout is one catalog per data root, so give each root its own `uvicorn.port`.
The repo-root `config.yml` is the legacy single-catalog setup for the shipped
`examples/` corpus, which names each data directory explicitly; new datasets should
use the `.tilewright/` layout instead.

Registration alone does not prove much: it never opens the data, and the
allowlist is only checked on read — so a dataset can register with `failed=0`
and serve nothing. That is why the skill's last gate reads an array back.

The dataset is now in the catalog:

```python
from tiled.client import from_uri
from tiled.queries import Key

c = from_uri("http://localhost:8017", api_key="tcbmin")
list(c)                                     # dataset keys
c["<NAME>"].search(Key("<param>") > 0)      # query entities by metadata
```
