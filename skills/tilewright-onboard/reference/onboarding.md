# tilewright — Dataset Onboarding Guide (contract v2)

## What this is

tilewright is a minimal broker that registers multi-modal scientific HDF5
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

You are on a host that can see the data (e.g. sdfiana025). Build the
environment once from the repo root:

```bash
cd <tilewright repo root>        # e.g. /sdf/.../cwang31/codes/tilewright
export UV_CACHE_DIR=/sdf/data/lcls/ds/prj/prjmaiqmag01/results/cwang31/.UV_CACHE   # S3DF only — omit elsewhere
uv sync          # once, creates .venv with tiled 0.2.x + deps
```

Then **work from the data root** — the directory holding the dataset you are
onboarding — and reach the CLI there with `uv run --project <tilewright repo
root> ...`, which selects the environment without changing the working
directory. Your outputs go in a `.tilewright/` directory inside that data
root:

```bash
cd <data root>
mkdir -p .tilewright/datasets .tilewright/manifests
```

Keeping the manifest beside the data it describes is what lets the
**tilewright-register** skill allowlist the data root once, so no dataset ever
needs a config edit. Every relative path in this guide is anchored to the data
root unless it says otherwise.

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

- `directory` (string, required) — absolute path, and write it **physical**
  (`readlink -f`), not through a symlink. Registration builds asset URIs from
  this string verbatim (`file://localhost{directory}/{file}`), and the server
  serves an asset only if that path is contained in an allowlisted root — a
  comparison that never resolves symlinks. Put `.tilewright/` in the data root
  and write `directory` physically, and the allowlist holds without ever naming
  this dataset: `directory` is that root or sits beneath it, so the
  **tilewright-register** skill's config covers it. A logical path that
  symlinks into the root is still refused, and `directory` pointing outside the
  root holding `.tilewright/` means a misplaced `.tilewright/` — neither is a
  config to widen. Both fail only at first read, never at registration.
- `pattern` (string, required) — glob relative to `directory`. Make it
  exclude non-HDF5 siblings (e.g. `*.h5` when the dir also holds `.nc`
  twins; `*/simulations.h5` to match one file per subdirectory).
- `params` (mapping or `null`, required) — where the per-entity physics
  parameters live inside each file. `params: null` is an explicit opt-in
  declaring the dataset has no per-entity params: entities are keyed by
  file path alone and their metadata is just `uid`. It is not a fallback —
  a `params` lookup that finds nothing is still a hard error. As a mapping,
  two keys, both required:
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

- `directory` here is where the **sidecar** lives — it need not be the data
  directory and need not be server-readable (no bytes are served).
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

## First run — inspect the data before writing any YAML

Do not reason about what the data "should" be; run this protocol and decide
from what you observe.

**Step 1 — list the directory.** Note every non-HDF5 sibling; your `pattern`
must exclude them all.

```bash
ls -la /abs/path/to/dataset_dir      # .nc twins? .parquet sidecars? .gz? subdirs?
```

**Step 2 — dump one candidate file completely** (every dataset's shape/dtype
and every attribute at every level, groups included):

```bash
uv run --project <tilewright repo root> python - <<'EOF'
import h5py
fp = "/abs/path/to/one_matched_file.h5"
with h5py.File(fp, "r") as f:
    for k, v in f.attrs.items():                     # root attrs (visititems skips "/")
        print(f"attr /@{k} = {v!r}")
    def dump(name, obj):
        if isinstance(obj, h5py.Dataset):
            print(f"D /{name}  shape={obj.shape} dtype={obj.dtype}")
        else:
            print(f"G /{name}/")
        for k, v in obj.attrs.items():
            print(f"attr /{name}@{k} = {v!r}")
    f.visititems(dump)
EOF
```

**Step 3 — read your observations off this table:**

| Observation in the dump | source tag | params | artifact membership |
|---|---|---|---|
| Scalar params are **attributes** on one group (often the root) | `files` | `{group: <that group>, from: attrs}` | every non-param dataset is an artifact |
| Scalar params are **0-d datasets** (`shape=()`) under one group | `files` | `{group: <that group>, from: datasets}` | every non-param dataset is an artifact |
| One group holds `(N,)` datasets and the big arrays are `(N, ...)` with the same leading N | `batch` | `{group: <that group>}` | only `(N, ...)` leading-axis datasets can be artifacts; everything else clients need (e.g. a `(151,)` axis) becomes a metadata path entry |
| Readable HDF5, arrays present, but **no scalar params anywhere** (no attrs, no 0-d datasets) | `files` | `null` — explicit opt-in: entities keyed by file path alone, metadata is just `uid` | every dataset is an artifact |
| h5py cannot open the files at all (not HDF5, or data lives at a remote facility), but a per-entity Parquet table exists or can be built | `table` | — (`id` = a unique column) | none: `artifacts` absent or `[]` |
| **Openable** HDF5 but no extractable scalar params at a single group (params scattered in nested subgroups) | prefer `files` + `params: null` if the arrays should be served; `table` if you have (or build) a per-entity sidecar and accept pointer-only | per that choice | per that choice |

The membership rule, stated once and binding: **files -> every non-param
dataset is an artifact; batch -> only (N, ...) leading-axis datasets can be
artifacts, everything else clients need becomes a metadata path entry.**
(That is why worked example 1 lists axis-like datasets such as `energy` and
`pixel` as artifacts, while worked example 2 puts `/eloss` in metadata: in
batch, a `(151,)` axis has no leading N and cannot be an artifact.)

**Step 4 — predict, then compare.** Count the matched files
(`ls <directory>/<pattern> | wc -l`) and predict the generator's output
before running it:

- `files`: entities = matched files; artifacts = entities × len(artifacts)
- `batch`: entities = sum of N over matched files; artifacts = entities × len(artifacts)
- `table`: entities = sidecar rows; artifacts = 0

The tool's summary line (`dataset=... entities=... artifacts=...`) must
equal your prediction exactly. If it does not, **stop** — do not register;
find the discrepancy (unmatched files, wrong N, unexpected rows) first.

## Limits and reserved names — what the contract cannot say

- **One params group only.** Params scattered across nested subgroups (e.g.
  `/instrument/Ei` carrying a `value` attr per subgroup) are unrepresentable
  as params. Route per the step-3 table: `files` + `params: null` if the
  arrays should be served, `table` + sidecar if you accept pointer-only.
- **No exclude mechanism.** Every attr (or 0-d dataset) under the params
  group is ingested, including housekeeping (`NX_class`, version strings).
  If that pollutes the metadata, there is no filter — accept it or report.
- **Batch params group is all-or-nothing.** Every dataset under a batch
  `params.group` must have leading axis N; a non-conforming dataset (an
  axis parked in the group) is a hard error, not skipped.
- **`(1,)`-shaped values are not 0-d scalars.** `from: datasets` takes only
  `shape=()` datasets; `(1,)` ones are silently skipped — check the dump.
- **Array-valued params become lists** in entity metadata — unqueryable by
  `Key` comparisons. Dead weight; and a param that is scalar in one file
  but an array in another kills Parquet writing (`ArrowInvalid`).
- **`uid` is reserved** everywhere (params, sidecar columns): it is the
  provenance hash. Generation refuses it.
- **Locator keys must not shadow sidecar columns** — a locator named like
  an existing column (e.g. `filename`) silently overwrites it.
- **Artifact `type` values must be unique** — each becomes a Tiled child
  key. Validation refuses duplicates.
- **`directory` must be absolute** — validation refuses relative paths
  (they would generate fine and break at serve time).
- **No sidecar table yet?** For a `table` source you may build one yourself
  with pandas (one row per entity, one unique id column,
  `df.to_parquet(...)`) and point `path` at it. That is allowed.

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

All from the **data root** (the directory holding `.tilewright/`) unless a step
says otherwise (on S3DF, with `UV_CACHE_DIR` exported — see Prerequisites). `--project`
points uv at the tilewright checkout for the environment; it does not change
the working directory, so the relative paths below stay anchored to the data
root.

`<KEY>` below is the `key:` value inside the dataset YAML (e.g. `BROAD_SIGMA`).
Name the YAML file and the manifest directory after it, exactly.

**1. Validate the contract only (touches no data):**
```bash
uv run --project <tilewright repo root> tilewright manifest .tilewright/datasets/<KEY>.yml --check
# -> contract OK: key=... source=files|batch|table artifacts=N
# invalid -> every error printed ("source.table requires 'id' ..."), exit 1
```

**2. Generate manifests:**
```bash
uv run --project <tilewright repo root> tilewright manifest .tilewright/datasets/<KEY>.yml -o .tilewright/manifests/<KEY>
# -> dataset=<KEY> entities=N artifacts=M -> .tilewright/manifests/<KEY>/...
```
This writes `entities.parquet` (uid + one column per parameter [+ extra,
locator columns]) and `artifacts.parquet`
(`uid,type,file,dataset,index,shape,dtype`; empty-but-typed for table
sources). Shape and dtype are captured now; registration never opens HDF5.

**3. Serving and registering — not this skill.**

Onboarding stops at Gate B. Configuring the catalog, starting the server,
registering the manifests, and proving an array reads back through HTTP belong
to the **tilewright-register** skill, which begins exactly where you stop:
`.tilewright/datasets/<KEY>.yml` + `.tilewright/manifests/<KEY>/`.

Because `.tilewright/` sits inside the data root and that skill's config
allowlists the data root itself, a newly onboarded dataset needs **no config
edit and no server restart** to become servable. Nothing you do here has to
anticipate the server.

**4. Tests** (from the tilewright repo root — these check the shipped corpus and
the source budgets, not your dataset):
```bash
uv run --with pytest pytest tests/ -v     # offline: corpus counts + budgets
uv run python tests/verify_live.py        # manual: needs the server running
```

## Error triage — symptom, cause, fix

Generation errors are prefixed with the offending filename and the YAML key
involved. Decode the common ones here before changing anything else:

| Symptom | Cause | Fix |
|---|---|---|
| `OSError: ... file signature not found` / `<file>: cannot open as HDF5` | `pattern` matched a non-HDF5 sibling (a `.nc` twin, a Parquet sidecar) | Tighten `pattern` (e.g. `"*.h5"`); re-check step 1 of the inspection protocol |
| Bare `KeyError: "... object 'X' doesn't exist"` with **no filename** | An HDF5 path is wrong in your own snippet (generation always prefixes the filename: `<file>: <yaml key> '/X' not found in file` means that path is absent in that matched file) | Run the step-2 dump on the named file; fix the group/dataset path in the YAML |
| `<file>: no params at group='...' from=...` | Wrong `group`, wrong `from` (attrs vs datasets), or the "scalars" are `(1,)`-shaped, which `from: datasets` skips | Check the dump: attrs on the group → `from: attrs`; `shape=()` datasets → `from: datasets`; `(1,)` shapes → unsupported, stop and report; genuinely no params anywhere in the file → `params: null` (explicit opt-in, step-3 table) |
| `sidecar column 'uid' is reserved` | The table sidecar carries a `uid` column — that name is the provenance hash | Rebuild the sidecar with the column renamed (e.g. `producer_uid`) or dropped, point `path` at the rebuilt file |
| `pyarrow.lib.ArrowInvalid` while writing manifests | One param changes type/shape across files (scalar in one file, array or string in another) | Dump two files and diff their params; fix the inconsistent one or report |
| `uid collision: N duplicate provenance ids` | Table `id` column is not unique (all-NaN ids hash identically), or duplicate rows | Choose a genuinely unique id column; dedupe the sidecar |
| `no files match '...' under ...` | Wrong `pattern` — or the `directory` itself does not exist (same message) | `ls` the directory first; test the glob: `ls <directory>/<pattern>` |

## Using the catalog — raw tiled cheat sheet

tiled's client IS the client; tilewright adds nothing on the HTTP path.
`<PORT>` below is the `uvicorn.port` in that data root's `.tilewright/config.yml`
— one catalog per data root, so each has its own (8017 is the conventional
first).

```python
from tiled.client import from_uri
from tiled.queries import Key

c = from_uri("http://localhost:<PORT>", api_key="tcbmin")   # your root's uvicorn.port
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
`index_<type>` locator metadata. `tilewright.client` parses it and does the
one non-trivial read (batched row index before user slice):

```python
from tiled.client import from_uri
from tiled.queries import Key
from tilewright import client as tw

c = from_uri("http://localhost:<PORT>", api_key="tcbmin")   # your root's uvicorn.port
ent = c["BROAD_SIGMA"].search(Key("sigma") >= 0.04).values().first()
tw.locate(ent)      # {"rixs_spectrum": {"file": ..., "dataset": ..., "index": ...}}
base = "/sdf/data/lcls/ds/prj/prjmaiqmag01/results/data-source/RIXS_SIM_BROAD_SIGMA"
spec = tw.load(ent, "rixs_spectrum", base)          # (151, 40), pure h5py
row0 = tw.load(ent, "rixs_spectrum", base, slc=(0, slice(None)))

# Table (pointer) entities have no artifact locators; locate() returns the
# entity metadata verbatim (sidecar columns + rendered locator columns):
cn = c["CNCS_incident_beam"].values().first()
tw.locate(cn)["globus_url"]
```

## Verify

Needs a registered dataset on a running server — i.e. after the
**tilewright-register** skill has done its job, not at the end of onboarding.
`--project` keeps this runnable from the data root:

```bash
uv run --project <tilewright repo root> python - <<'EOF'
from tiled.client import from_uri
c = from_uri("http://localhost:<PORT>", api_key="tcbmin")   # your root's uvicorn.port
print(list(c))                          # dataset keys
ds = c[list(c)[0]]
ent = ds.values().first()
print(dict(ent.metadata))               # physics params + locators
print({k: ent[k].shape for k in ent} if len(ent) else "pointer-only entity")
EOF
```
