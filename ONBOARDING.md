# tcb-min — Dataset Onboarding Guide

## What this is

tcb-min is a minimal broker that registers multi-modal scientific HDF5
datasets into a [Tiled](https://blueskyproject.io/tiled/) catalog without
copying any data: files are referenced in place, and physics parameters
become server-side queryable metadata. You describe a dataset once in a small
YAML file, generate two Parquet manifests from it, and register those
manifests into a running Tiled server over HTTP. The hierarchy is
**Dataset** (container with provenance metadata) → **Entity** (container
whose metadata is the physics parameters) → **Artifact** (array child served
lazily from the source HDF5 file).

Everything is explicit. The YAML is the contract; nothing about your data is
guessed. If the YAML doesn't say it, it doesn't happen.

## Prerequisites

You are on a host that can see the data (e.g. sdfiana025). Always export the
uv cache first, and run everything from the repo root:

```bash
cd /sdf/data/lcls/ds/prj/prjmaiqmag01/results/cwang31/codes/tcb-min
export UV_CACHE_DIR=/sdf/data/lcls/ds/prj/prjmaiqmag01/results/cwang31/.UV_CACHE
uv sync          # once, creates .venv with tiled 0.2.9 + deps
```

## The dataset YAML contract — field by field

```yaml
key: MY_DATASET            # required
label: "My Dataset"        # optional
metadata:                  # required; data_type required, everything else open
  data_type: experimental
data:                      # required
  directory: /abs/path
  layout: per_entity       # per_entity | batched | pointer
  file_pattern: "*.h5"     # per_entity/batched only (required there)
  sidecar: table.parquet   # pointer only (required there)
parameters:                # required
  location: root_attributes  # root_scalars | root_attributes | group | sidecar
  group: /params           # required iff location == group
artifacts:                 # required; min 1 unless layout == pointer (then [])
  - {type: spectrum, dataset: /spectra}
shared: []                 # optional; per_entity/batched only
extra_metadata: []         # optional; per_entity/batched only
locator: {}                # optional; pointer only
provenance: {}             # optional; free-form dict
```

Unknown top-level keys are **rejected** (typos fail loudly).

### `key` (string, required)
The Tiled key of the dataset container, e.g. `BROAD_SIGMA`. Entity keys are
derived from it: `{key}_{uid[:13]}`.

### `label` (string, optional)
Human-readable name. Informational only.

### `metadata` (mapping, required)
Becomes the dataset container's metadata verbatim. `data_type` is the only
required field (typical values: `experimental`, `simulation`, `benchmark`).
Add anything else that describes provenance: `material`, `method`,
`facility`, `producer`, `instrument`, `pi`, ...

### `data` (mapping, required)
- `directory` — absolute path to the dataset root. All `file` columns in the
  manifests are relative to it, and registration builds asset URIs as
  `file://localhost{directory}/{file}`. The **server's** `config.yml` must
  list this directory (or a parent) under `readable_storage`, or reads will
  be refused.
- `layout` — one of:
  - `per_entity` — **one file = one entity**. Each matched file yields one
    entity; every artifact dataset must exist in every file.
  - `batched` — **entities are rows along axis 0** of stacked datasets, in
    one or more files. If `/spectra` is `(2000, 151, 40)`, that file holds
    2000 entities and entity *i*'s artifact is `spectra[i]` with shape
    `(151, 40)`. Row indices reset per file; the `file` column disambiguates.
  - `pointer` — **no locally readable artifact bytes** (files are elsewhere,
    e.g. Globus, or in a non-HDF5 format). Entities come from a sidecar
    Parquet table, one row per entity; there are zero array children and
    all access goes through locator metadata.
- `file_pattern` — glob relative to `directory`, required for
  per_entity/batched, forbidden for pointer. Make it exclude non-HDF5
  siblings (e.g. use `*.h5` when the dir also holds `.nc`/`.csv` twins;
  use `*/simulations.h5` to match one file per subdirectory).
- `sidecar` — Parquet filename relative to `directory`, required for
  pointer, forbidden otherwise.

### `parameters` (mapping, required)
Where the per-entity physics parameters live. Open one data file (or the
sidecar) and look at where the numbers actually are:

- `root_scalars` — 0-dimensional datasets at the HDF5 root
  (`f["/Ja_meV"][()]`). per_entity only.
- `root_attributes` — attributes on the HDF5 root (`f.attrs`). 1-element
  arrays are unwrapped to scalars; bytes are decoded to str. per_entity only.
- `group` — datasets under `parameters.group`. For per_entity each is read
  as a scalar; for batched each must be a `(N,)` dataset whose row *i*
  belongs to entity *i*. `group` (the HDF5 path, e.g. `/params`) is required
  with this location and forbidden with the others.
- `sidecar` — every column of the sidecar Parquet is a parameter. pointer
  only (and pointer requires exactly this location).

Valid combinations: per_entity × {root_scalars, root_attributes, group};
batched × {group}; pointer × {sidecar}. Anything else is rejected at
validation time.

### `artifacts` (list, required)
The arrays to serve, `{type, dataset}` each. `type` becomes the Tiled key of
the array child (e.g. `client[KEY][entity_key]["rixs_spectrum"]`), `dataset`
is the HDF5 path inside each file. Minimum 1 entry — except pointer layout,
which must have `artifacts: []`. Every listed dataset must exist in every
matched file (missing → hard error).

### `shared` (list, optional; per_entity/batched)
Axes/grids identical across entities, `{type, dataset}` each. They are NOT
registered as arrays; they surface as `shared_dataset_<type>` locator strings
on the **dataset** container metadata so a client can fetch them once via
h5py from any source file.

### `extra_metadata` (list, optional; per_entity/batched)
Per-entity values that should ride along as entity metadata but are
**excluded from the uid hash** (e.g. derived quantities like `/log_probs`).
One `{dataset: /path}` per entry; the column is named after the last path
segment. For batched, the dataset must be `(N,)`-aligned with the entities.

### `locator` (mapping, optional; pointer only)
`{column_name: template}` string templates rendered per sidecar row;
`{colname}` placeholders interpolate that row's columns. Constants (no
placeholders) are allowed. Rendered columns land in entity metadata, so a
client can build e.g. a Globus download URL for every entity.

### `provenance` (mapping, optional)
Free-form dict merged into the dataset container metadata at registration
(e.g. `created_at`, `code_commit`).

## How to decide `layout`

1. Does one file correspond to one physical entity (one sample, one scan,
   one simulation)? → `per_entity`.
2. Are many entities stacked along axis 0 of big datasets (params as `(N,)`
   arrays, data as `(N, ...)` arrays)? → `batched`.
3. Can Tiled not read the bytes at all (non-HDF5 format, or data lives at a
   remote facility) but a table of per-file parameters exists? → `pointer`.

## How to decide `parameters.location`

Open one file with h5py and look:

```python
import h5py
f = h5py.File("sample.h5", "r")
dict(f.attrs)                 # non-empty? -> root_attributes
[k for k in f if f[k].shape == ()]        # 0-dim root datasets? -> root_scalars
list(f["/params"])            # a group of named param datasets? -> group
```

For pointer layouts, the parameters are the columns of the sidecar Parquet →
`sidecar`.

## Worked example 1 — per_entity (LCLS RIXS static scans)

Directory `/sdf/.../data-source/LS/static` holds `S_52.h5` (plus a `.nc` twin
that must be excluded). Params are root attributes (`twotheta`, `chi`, ...);
the 9 root datasets are all artifacts.

```yaml
key: LCLS_RIXS_STATIC
label: "LCLS RIXS Static"
metadata:
  data_type: experimental
  material: NiPS3
  method: RIXS
  facility: LCLS
data:
  directory: /sdf/data/lcls/ds/prj/prjmaiqmag01/results/data-source/LS/static
  layout: per_entity
  file_pattern: "*.h5"
parameters:
  location: root_attributes
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

## Worked example 2 — batched (RIXS simulation sweep)

Directory `/sdf/.../data-source/RIXS_SIM_BROAD_SIGMA` holds
`batch_0/simulations.h5` ... `batch_4/simulations.h5`. Each file:
`/spectra (2000, 151, 40)`, `/params/<12 names> (2000,)`,
`/log_probs (2000,)`, shared axes `/eloss (151,)` and `/omega_bounds (2,)`.

```yaml
key: BROAD_SIGMA
label: "Broad Sigma"
metadata:
  data_type: simulation
  material: NiPS3
data:
  directory: /sdf/data/lcls/ds/prj/prjmaiqmag01/results/data-source/RIXS_SIM_BROAD_SIGMA
  layout: batched
  file_pattern: "*/simulations.h5"
parameters:
  location: group
  group: /params
artifacts:
  - {type: rixs_spectrum, dataset: /spectra}
shared:
  - {type: eloss, dataset: /eloss}
  - {type: omega_bounds, dataset: /omega_bounds}
extra_metadata:
  - dataset: /log_probs
```

Expected: `entities=10000 artifacts=10000` (each artifact served as
`(151, 40)` float64).

## Worked example 3 — pointer (CNCS incident-beam, Globus-hosted)

Directory `/sdf/.../data-source/19g/mcstas_incident_beam/cncs_new` holds 100
`.mcpl.gz` event files (not Tiled-readable) plus `CNCS_srtd.parquet` — 100
rows × 10 columns (`Ei, resmode, speed1..speed5, Instr, T0, filename`).

```yaml
key: CNCS_incident_beam
label: "CNCS Incident Beam (McStas, SNS/ORNL)"
metadata:
  data_type: simulation
  producer: McStas
  facility: SNS/ORNL
  instrument: CNCS
  pi: "G. E. Granroth"
data:
  directory: /sdf/data/lcls/ds/prj/prjmaiqmag01/results/data-source/19g/mcstas_incident_beam/cncs_new
  layout: pointer
  sidecar: CNCS_srtd.parquet
parameters:
  location: sidecar
artifacts: []
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
# -> contract OK: key=... layout=... location=... artifacts=N
```

**2. Generate manifests:**
```bash
uv run python -m tcb_min.manifest datasets/my_dataset.yml -o manifests/MY_DATASET
# -> dataset=MY_DATASET entities=N artifacts=M -> manifests/MY_DATASET/...
```
This writes `entities.parquet` (uid + one column per parameter [+ extras,
locators]) and `artifacts.parquet`
(`uid,type,file,dataset,index,shape,dtype,file_size,file_mtime`). Shape and
dtype are captured now; registration never opens HDF5.

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
Re-running is safe: already-registered entities are counted as `skipped`.

## Verify

```bash
uv run python - <<'EOF'
from tcb_min import client as tcb
c = tcb.connect("http://localhost:8017", api_key="tcbmin")
print(list(c))                          # dataset keys
ds = c[list(c)[0]]
ent = ds.values().first()
print(dict(ent.metadata))               # physics params + locators
print({k: e.read().shape for k, e in ent.items()} if len(ent) else tcb.locate(ent))
EOF
```

Query and fetch (Mode B), all server-side:

```python
from tcb_min import client as tcb
c = tcb.connect("http://localhost:8017", api_key="tcbmin")
hits = tcb.find(c, "BROAD_SIGMA", sigma=(0.04, 0.05))   # SQL-backed range query
ent = hits.values().first()
spec = tcb.fetch(ent, "rixs_spectrum")                   # (151, 40) numpy array
blob = tcb.export_entity(ent)                            # whole entity as HDF5 bytes
```

For Mode A (direct h5py access) use `tcb.locate(ent)` — it returns the
`path_<type>` / `dataset_<type>` / `index_<type>` (and for pointer datasets
`globus_*`) locator metadata; join `path_*` with the dataset `directory`.
