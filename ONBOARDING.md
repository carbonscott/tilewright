# tcb-min — Dataset Onboarding Guide (contract v2)

## What this is

tcb-min is a minimal broker that registers multi-modal scientific HDF5
datasets into a [Tiled](https://blueskyproject.io/tiled/) catalog without
copying any data: files are referenced in place, and physics parameters
become server-side queryable metadata. You describe a dataset once in a
small YAML file, generate two Parquet manifests from it, and register those
manifests into a running Tiled server over HTTP. The hierarchy is
**Dataset** (container with provenance metadata) → **Entity** (container
whose metadata is the physics parameters) → **Artifact** (array child
served lazily from the source HDF5 file).

Everything is explicit. The YAML is the contract; nothing about your data
is guessed. If the YAML doesn't say it, it doesn't happen. Invalid YAML
prints **every** error in plain language and exits 1 — no type coercion,
exact types or fail loud.

## Prerequisites

You are on a host that can see the data (e.g. sdfiana025). Always export
the uv cache first, and run everything from the repo root:

```bash
cd /sdf/data/lcls/ds/prj/prjmaiqmag01/results/cwang31/codes/tcb-min
export UV_CACHE_DIR=/sdf/data/lcls/ds/prj/prjmaiqmag01/results/cwang31/.UV_CACHE
uv sync          # once, creates .venv with tiled 0.2.x + deps
```

## The dataset YAML contract — field by field

Exactly four top-level keys are allowed: `key`, `metadata`, `source`,
`artifacts`. Unknown keys are rejected (typos fail loudly).

```yaml
key: MY_DATASET            # required: Tiled key of the dataset container
metadata:                  # required: free-form dict; data_type required
  data_type: experimental
source:                    # required: TAGGED UNION — exactly ONE of the three
  files:  {...}            #   one matched file = one entity
  batch:  {...}            #   entities stacked along axis 0 inside each file
  table:  {...}            #   sidecar Parquet rows ARE the entities
artifacts:                 # required (min 1) for files/batch;
  - {type: spectrum, dataset: /spectra}   # absent or [] for table
```

### `key` (string, required)

The Tiled key of the dataset container, e.g. `BROAD_SIGMA`. Entity keys are
derived from it: `{key}_{uid[:13]}` where `uid` is a provenance hash (see
"Entity identity" below).

### `metadata` (mapping, required)

Becomes the dataset container's metadata verbatim. `data_type` is the only
required field (typical values: `experimental`, `simulation`, `benchmark`).
Add anything else that describes provenance: `material`, `method`,
`facility`, `producer`, `instrument`, `pi`, ... Axes/grids shared by all
entities (an energy-loss axis, omega bounds) are just entries you write
here yourself, e.g. `shared_eloss: /eloss` — a client fetches them once via
h5py from any source file. There is no special mechanism for them.

### `source` (mapping, required) — the tagged union

Exactly one of `files`, `batch`, `table`. Each tag owns only its own keys;
an illegal combination is unrepresentable, not merely rejected.

**`source.files`** — per-entity: one matched file = one entity.

```yaml
source:
  files:
    directory: /abs/path        # dataset root; file columns are relative to it
    pattern: "*.h5"             # glob relative to directory
    params: {group: "/", from: attrs}
```

- `directory` (string, required) — absolute path. Registration builds asset
  URIs as `file://localhost{directory}/{file}`, and the **server's**
  `config.yml` must list this directory (or a parent) under
  `readable_storage` or reads will be refused.
- `pattern` (string, required) — glob relative to `directory`. Make it
  exclude non-HDF5 siblings (e.g. `*.h5` when the dir also holds `.nc`
  twins; `*/simulations.h5` to match one file per subdirectory).
- `params` (mapping, required) — where the per-entity physics parameters
  live inside each file. Two keys, both required:
  - `group` — the HDF5 group to look in (`"/"` for the root, `/params`
    for a named group).
  - `from` — one of exactly two values:
    - `attrs` — parameters are HDF5 **attributes** on that group
      (`f[group].attrs`).
    - `datasets` — parameters are **0-dimensional datasets** directly
      under that group (`f[group]["Ja_meV"][()]`). Non-scalar datasets
      under the group (e.g. your artifact arrays at `/`) are not params.

**`source.batch`** — entities are rows along axis 0 inside each matched file.

```yaml
source:
  batch:
    directory: /abs/path
    pattern: "*/simulations.h5"
    params: {group: /params}
    extra: [/log_probs]          # optional
```

- `directory`, `pattern` — as for `files`.
- `params` (mapping, required) — only `group`. Every `(N,)` dataset under
  that group is one param column; row *i* belongs to entity *i*. There is
  no `from` here: batch params are always datasets.
- `extra` (list of HDF5 paths, optional) — root datasets that are
  axis-0-matched with the entities (`(N,)` leading axis); each becomes one
  entity metadata column named after its last path segment (e.g.
  `/log_probs` → column `log_probs`).

If `/spectra` is `(2000, 151, 40)`, that file holds 2000 entities and
entity *i*'s artifact is `spectra[i]` with shape `(151, 40)`. Row indices
reset per file; the manifest's `file` column disambiguates.

**`source.table`** — passthrough: the sidecar Parquet's rows ARE the
entities. Use it when Tiled cannot read the bytes at all (non-HDF5 format,
or the data lives at a remote facility) but a table of per-file parameters
exists. Zero array children; all access goes through metadata.

```yaml
source:
  table:
    directory: /abs/path
    path: CNCS_srtd.parquet      # Parquet filename relative to directory
    id: filename                 # required: column that identifies each entity
    locator:                     # optional: rendered per row
      globus_path: "/maiqmag/.../{filename}"
```

- `path` (string, required) — the sidecar Parquet, relative to `directory`.
  One row per entity; every column becomes queryable entity metadata.
- `id` (string, required) — the column whose value identifies the entity.
  It must be unique across rows (duplicates are a hard error) and it feeds
  the provenance uid.
- `locator` (mapping, optional) — `{column_name: template}` string
  templates rendered per row; `{colname}` placeholders interpolate that
  row's columns. Constants (no placeholders) are allowed. Rendered columns
  land in entity metadata, so a client can build e.g. a Globus download URL
  for every entity.

### `artifacts`

The arrays to serve, `{type, dataset}` each. `type` becomes the Tiled key
of the array child (`client[KEY][entity_key]["rixs_spectrum"]`); `dataset`
is the HDF5 path inside each file. Rules:

- `files` / `batch`: required, minimum 1 entry. Every listed dataset must
  exist in every matched file (missing → hard error). For `batch`, every
  artifact dataset's leading axis must equal N.
- `table`: must be **absent or `[]`** — table entities have no readable
  bytes.

## Entity identity (uid)

The uid is a hash of **provenance** (where the entity came from), never of
its parameters:

| source | uid |
|---|---|
| files | `sha256(relative_file_path)[:16]` |
| batch | `sha256("relative_file_path:row_index")[:16]` |
| table | `sha256(str(row[id]))[:16]` |

Consequence: two files with identical params are **two entities**. That is
correct — identity is where the data came from; params are pure queryable
metadata and may collide freely. Regenerating a manifest is stable as long
as file names (or table ids) don't change.

## How to choose the source tag — look at the data

1. Does one file correspond to one physical entity (one sample, one scan,
   one simulation)? → `files`.
2. Are many entities stacked along axis 0 of big datasets (params as
   `(N,)` arrays, data as `(N, ...)` arrays)? → `batch`.
3. Can Tiled not read the bytes at all, but a Parquet of per-entity rows
   exists? → `table`.

## How to choose `params.from` (files only) — look where the numbers live

```python
import h5py
f = h5py.File("sample.h5", "r")
dict(f.attrs)                              # non-empty? -> {group: "/", from: attrs}
[k for k in f if f[k].shape == ()]         # 0-dim root datasets? -> {group: "/", from: datasets}
list(f["/params"])                         # named group of scalars? -> {group: /params, from: datasets}
```

## Worked example 1 — files (LCLS RIXS static scans)

Directory `/sdf/.../data-source/LS/static` holds `S_52.h5` (plus a `.nc`
twin that must be excluded). Params are root attributes (`twotheta`,
`chi`, ...); the 9 root datasets are all artifacts.

```yaml
key: LCLS_RIXS_STATIC
metadata:
  data_type: experimental
  material: NiPS3
  method: RIXS
  facility: LCLS
source:
  files:
    directory: /sdf/data/lcls/ds/prj/prjmaiqmag01/results/data-source/LS/static
    pattern: "*.h5"
    params: {group: "/", from: attrs}
artifacts:
  - {type: I0, dataset: /I0}
  - {type: I_rn, dataset: /I_rn}
  - {type: S, dataset: /S}
  - {type: counts, dataset: /counts}
  - {type: energy, dataset: /energy}
  - {type: motor, dataset: /motor}
  - {type: motor_step, dataset: /motor_step}
  - {type: pixel, dataset: /pixel}
  - {type: rn, dataset: /rn}
```

Expected: `entities=1 artifacts=9`.

## Worked example 2 — batch (RIXS simulation sweep)

Directory `/sdf/.../data-source/RIXS_SIM_BROAD_SIGMA` holds
`batch_0/simulations.h5` ... `batch_4/simulations.h5`. Each file:
`/spectra (2000, 151, 40)`, `/params/<12 names> (2000,)`,
`/log_probs (2000,)`, shared axes `/eloss (151,)` and `/omega_bounds (2,)`.

```yaml
key: BROAD_SIGMA
metadata:
  data_type: simulation
  material: NiPS3
  shared_eloss: /eloss            # plain metadata: shared axes are entries
  shared_omega_bounds: /omega_bounds   # the author writes, nothing more
source:
  batch:
    directory: /sdf/data/lcls/ds/prj/prjmaiqmag01/results/data-source/RIXS_SIM_BROAD_SIGMA
    pattern: "*/simulations.h5"
    params: {group: /params}
    extra: [/log_probs]
artifacts:
  - {type: rixs_spectrum, dataset: /spectra}
```

Expected: `entities=10000 artifacts=10000` (each artifact served as
`(151, 40)` float64).

## Worked example 3 — table (CNCS incident-beam, Globus-hosted)

Directory `/sdf/.../data-source/19g/mcstas_incident_beam/cncs_new` holds
100 `.mcpl.gz` event files (not Tiled-readable) plus `CNCS_srtd.parquet` —
100 rows × 10 columns (`Ei, resmode, speed1..speed5, Instr, T0, filename`).

```yaml
key: CNCS_incident_beam
metadata:
  data_type: simulation
  producer: McStas
  facility: SNS/ORNL
  instrument: CNCS
  pi: "G. E. Granroth"
source:
  table:
    directory: /sdf/data/lcls/ds/prj/prjmaiqmag01/results/data-source/19g/mcstas_incident_beam/cncs_new
    path: CNCS_srtd.parquet
    id: filename
    locator:
      mcpl_filename: "{filename}"
      globus_endpoint: "ee51784b-3173-4ff2-bab5-38c4bd867d02"
      globus_path: "/maiqmag/19g/mcstas_incident_beam/cncs_new/{filename}"
      globus_url: "https://app.globus.org/file-manager?origin_id=ee51784b-3173-4ff2-bab5-38c4bd867d02&origin_path=/maiqmag/19g/mcstas_incident_beam/cncs_new/{filename}"
```

Expected: `entities=100 artifacts=0`. Each entity's metadata carries its
physics params plus `globus_url` etc.; there are no array children.

## Commands

All from the repo root, with `UV_CACHE_DIR` exported (see Prerequisites).

**1. Validate the contract only (touches no data):**
```bash
uv run python -m tcb_min.manifest datasets/my_dataset.yml --check
# -> contract OK: key=... source=files|batch|table artifacts=N
# invalid -> every error printed ("source.table requires 'id' ..."), exit 1
```

**2. Generate manifests:**
```bash
uv run python -m tcb_min.manifest datasets/my_dataset.yml -o manifests/MY_DATASET
# -> dataset=MY_DATASET entities=N artifacts=M -> manifests/MY_DATASET/...
```
This writes `entities.parquet` (uid + one column per parameter [+ extra,
locator columns]) and `artifacts.parquet`
(`uid,type,file,dataset,index,shape,dtype`; empty-but-typed for table
sources). Shape and dtype are captured now; registration never opens HDF5.

**3. Start the server** (its own terminal; leave it running):
```bash
uv run tiled serve config config.yml --api-key tcbmin
# serves http://127.0.0.1:8017; creates ./catalog.db on first run
```
If your dataset directory is new, add it under `readable_storage` in
`config.yml` first.

**4. Register:**
```bash
uv run python -m tcb_min.register datasets/my_dataset.yml \
    --manifests manifests/MY_DATASET --url http://localhost:8017 --api-key tcbmin
# -> dataset=MY_DATASET entities_added=N artifacts_added=M skipped=0 failed=0
```
Re-running is safe: an already-complete entity counts as `skipped`. An
entity whose array-children count disagrees with the manifest (a crashed
earlier run) prints a loud WARNING and counts as `failed` — delete it and
re-register.

**5. Tests:**
```bash
uv run --with pytest pytest tests/ -v     # offline: corpus counts + budgets
uv run python tests/verify_live.py        # manual: needs the server running
```

## Using the catalog — raw tiled cheat sheet

tiled's client IS the client; tcb-min adds nothing on the HTTP path.

```python
from tiled.client import from_uri
from tiled.queries import Key

c = from_uri("http://localhost:8017", api_key="tcbmin")
list(c)                                   # dataset keys
dict(c["BROAD_SIGMA"].metadata)           # dataset provenance metadata
ds = c["BROAD_SIGMA"]
len(ds)                                   # entity count

# SQL-served metadata queries (Key comparisons only; never Regex):
hits = ds.search(Key("sigma") >= 0.04).search(Key("sigma") <= 0.05)
hits = hits.search(Key("gamma") == 0.1)   # chain freely
ent = hits.values().first()
dict(ent.metadata)                        # physics params + Mode-A locators

# Sliced reads — the server reads only the requested bytes:
arr = ent["rixs_spectrum"]
arr.shape                                 # (151, 40)
arr[0:5, :]                               # numpy array, lazy adapter

# Bulk export — whole entity as one HDF5 blob, single round trip:
import io
buf = io.BytesIO()
ent.export(buf, format="application/x-hdf5")
open("entity.h5", "wb").write(buf.getvalue())
```

## Mode A — direct h5py access (same-filesystem readers)

Every registered entity carries `path_<type>` / `dataset_<type>` /
`index_<type>` locator metadata. `tcb_min.client` parses it and does the
one non-trivial read (batched row index before user slice):

```python
from tiled.client import from_uri
from tiled.queries import Key
from tcb_min import client as tcb

c = from_uri("http://localhost:8017", api_key="tcbmin")
ent = c["BROAD_SIGMA"].search(Key("sigma") >= 0.04).values().first()
tcb.locate(ent)      # {"rixs_spectrum": {"file": ..., "dataset": ..., "index": ...}}
base = "/sdf/data/lcls/ds/prj/prjmaiqmag01/results/data-source/RIXS_SIM_BROAD_SIGMA"
spec = tcb.load(ent, "rixs_spectrum", base)          # (151, 40), pure h5py
row0 = tcb.load(ent, "rixs_spectrum", base, slc=(0, slice(None)))

# Table (pointer) entities have no artifact locators; locate() returns the
# entity metadata verbatim (sidecar columns + rendered locator columns):
cn = c["CNCS_incident_beam"].values().first()
tcb.locate(cn)["globus_url"]
```

## Verify

```bash
uv run python - <<'EOF'
from tiled.client import from_uri
c = from_uri("http://localhost:8017", api_key="tcbmin")
print(list(c))                          # dataset keys
ds = c[list(c)[0]]
ent = ds.values().first()
print(dict(ent.metadata))               # physics params + locators
print({k: ent[k].shape for k in ent} if len(ent) else "pointer-only entity")
EOF
```
