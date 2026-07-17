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

The YAML contract is a tagged union — `source: files | batch | table | groups` — plus a
`key`, provenance `metadata`, and an `artifacts` list. See `examples/datasets/` for
worked examples and `skills/tilewright-onboard/reference/onboarding.md` for the full
field reference. Copy their *structure*, not their filenames: those predate the
`<KEY>` convention above (`broad_sigma.yml` holds `key: BROAD_SIGMA`).

### 2. Register — the `tilewright-register` skill

Run the **`tilewright-register`** skill (`skills/tilewright-register/`). You hand it
a Tiled endpoint that is **already running** — its `<URL>` and an `<API_KEY>` carrying
write scopes; the skill does not start a server. It first proves the endpoint resolves
the same absolute paths your manifests carry, then registers, then reads one array
back through HTTP to prove the bytes actually flow:

```bash
cd <data root>
uv run --project <tilewright repo root> tilewright register .tilewright/datasets/<KEY>.yml \
  --manifests .tilewright/manifests/<KEY> \
  --url <URL> --api-key <API_KEY>          # always pass both: the defaults point at a local server
```

Whoever deploys that endpoint owns its `readable_storage` allowlist, and it is not
yours to edit. If you have no endpoint — or the one you were given cannot resolve your
paths —
[`self-hosted-server.md`](skills/tilewright-register/reference/self-hosted-server.md)
covers running your own server for testing, including the impostor check that proves
the server answering on the port is the one you started (a log line cannot). The
repo-root `config.yml` is the legacy single-catalog setup for the shipped `examples/`
corpus, which names each data directory explicitly; new datasets should use the
`.tilewright/` layout instead.

Registration alone does not prove much: it never opens the data, and the
allowlist is only checked on read — so a dataset can register with `failed=0`
and serve nothing. That is why the skill's last gate reads an array back.

The dataset is now in the catalog:

```python
from tiled.client import from_uri
from tiled.queries import Key

c = from_uri("<URL>", api_key="<API_KEY>")  # the endpoint you registered into
list(c)                                     # dataset keys
c["<KEY>"].search(Key("<param>") > 0)       # query entities by metadata
```

For more — chained metadata queries, sliced and bulk reads, and Mode A (direct h5py
access for readers on the same filesystem as the data) — see
[`docs/using-the-catalog.md`](docs/using-the-catalog.md).
