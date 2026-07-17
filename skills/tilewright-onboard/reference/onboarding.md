# tilewright — dataset YAML field reference (contract v2)

## What this is

tilewright is a minimal broker that registers multi-modal scientific HDF5
datasets into a [Tiled](https://blueskyproject.io/tiled/) catalog without
copying any data: files are referenced in place, and physics parameters
become server-side queryable metadata. The hierarchy is **Dataset**
(container with provenance metadata) → **Entity** (container whose metadata
is the physics parameters) → **Artifact** (array child served lazily from
the source HDF5 file).

Everything is explicit. The YAML is the contract; nothing about your data
is guessed. If the YAML doesn't say it, it doesn't happen. Invalid YAML
prints **every** error in plain language and exits 1 — no type coercion,
exact types or fail loud.

This file is the **field reference**: what every key means, what the contract
cannot say, four worked examples, and how to decode an error. The **procedure** —
what to inspect, which source tag to choose, what counts to predict, and the two
gates that define done — is `SKILL.md`, and it is deliberately not repeated here.
Where this file needs a decision, it points at `SKILL.md` by name.

That split is load-bearing, not stylistic: this file and `SKILL.md` once both
carried the procedure, the copies drifted, and the reference silently went stale
on a whole source tag. If you are about to add a decision table, a gate, or an
inspection step here — it belongs in `SKILL.md` instead.

## Contents

| Section | Read it when |
|---|---|
| The dataset YAML contract — field by field | authoring the YAML: every key, every tag, what each accepts |
| Entity identity (uid) | asking why two identical-looking files are two entities |
| Limits and reserved names | the contract will not say what you want it to say |
| Worked examples 1–4 (files / batch / table / groups) | starting — copy the closest one and adapt it |
| What the generated manifest contains | the generator ran; what did it write |
| Error triage — symptom, cause, fix | a `tilewright manifest` command failed |

## The dataset YAML contract — field by field

Exactly four top-level keys are allowed: `key`, `metadata`, `source`,
`artifacts`. Unknown keys are rejected (typos fail loudly).

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

Exactly one of `files`, `batch`, `table`, `groups`. Each tag owns only its own
keys; an illegal combination is unrepresentable, not merely rejected.

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
  comparison that never resolves symlinks. A logical path that symlinks into an
  allowlisted root is still refused, and so is a `directory` no allowlisted root
  contains. Both fail only at first read, never at registration. That allowlist
  belongs to whoever deploys the endpoint and is not yours to widen — writing
  `directory` physically is what keeps the question from arising.
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
- `server_base_dir` (string, optional) — the serving host's own view of
  `directory`, substituted for it **only** when building the asset URI; every
  local read keeps using `directory`. Legal on `files`, `batch` and `groups`,
  never on `table`. Leave it out while onboarding: you cannot know the server's
  view from here, and omitting it emits a byte-identical URI. It is the
  **tilewright-register** skill's to add, once that skill has measured what the
  endpoint actually resolves.

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

**`source.groups`** — entities are sibling top-level groups inside ONE file.

```yaml
source:
  groups:
    directory: /abs/path
    file: nips3_20000.h5         # ONE file, relative to directory
    pattern: "sample_*"          # globs top-level group NAMES, not HDF5 paths
    params: {group: params, from: datasets}
```

Use it when a producer wrote N self-contained entities into one HDF5 —
`/sample_1`, `/sample_2`, ... — each with its own params and its own
**unstacked** arrays. `files` would collapse that to a single entity, and
`batch` cannot read it: nothing is stacked on a leading axis.

- `directory` — as for `files` (absolute, physical).
- `file` (string, required) — the one file, **relative to `directory`** and
  free of `..`. It is joined onto the server's view of the root to build the
  asset URI, so an absolute path would silently discard `server_base_dir` and
  strand the URI on the generating host's path — rejected by validation.
- `pattern` (string, required) — an fnmatch glob over **top-level group
  names** (`sample_*`), not paths (`/sample_*`). Only groups become entities:
  a top-level *dataset* whose name matches is skipped, never an entity.
  Matching nothing is a hard error, not an empty dataset.
- `params` (mapping or `null`, required) — the same `{group, from}` mapping as
  `files`, one level down: `group` is resolved **inside each entity's group**,
  so `group: params` reads `/sample_N/params`. `from: datasets` is the common
  case (0-d datasets); `from: attrs` reads that subgroup's attributes.

Artifacts are declared **relative to the entity group** (`data`, not
`/sample_1/data`) and emitted absolute per entity, so each entity serves its
own `/sample_N/data`. A leading `/` is rejected by validation.

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
- `groups`: required, minimum 1 entry, but `dataset` is written **relative to
  the entity group** (`data`, not `/sample_1/data` — a leading `/` is
  rejected). It must exist in every matched group.
- `table`: must be **absent or `[]`** — table entities have no readable
  bytes.

## Entity identity (uid)

The uid is a hash of **provenance** (where the entity came from), never of
its parameters:

| source | uid |
|---|---|
| files | `sha256(relative_file_path)[:16]` |
| batch | `sha256("relative_file_path:row_index")[:16]` |
| groups | `sha256("relative_file_path:/group_name")[:16]` |
| table | `sha256(str(row[id]))[:16]` |

Consequence: two files with identical params are **two entities**. That is
correct — identity is where the data came from; params are pure queryable
metadata and may collide freely. Regenerating a manifest is stable as long
as file names (or table ids) don't change.

## Limits and reserved names — what the contract cannot say

- **One params group only.** Params scattered across nested subgroups (e.g.
  `/instrument/Ei` carrying a `value` attr per subgroup) are unrepresentable
  as params. Route per the source-tag decision table in `SKILL.md`: `files` +
  `params: null` if the arrays should be served, `table` + sidecar if you accept
  pointer-only.
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

`/eloss` is metadata here, not an artifact, because a `(151,)` axis has no
leading N — in `batch`, only `(N, ...)` datasets can be artifacts. Contrast
worked example 1, where the same axis-like datasets (`energy`, `pixel`) *are*
artifacts: `files` has no leading-axis constraint. The two examples are not
inconsistent; the source tag is what differs.

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

## Worked example 4 — groups (NiPS3 9-DOF sweep, 20,000 entities in one file)

Directory `/sdf/.../data-source/Zhantao` holds a single 19GB
`nips3_fwhm4_9dof_20000_20260303_0537.h5`. Inside it: 20,000 sibling
top-level groups `/sample_1` ... `/sample_20000`, each holding
`params/<9 names>` as `shape=()` **datasets** (the groups' attrs are empty)
plus its own unstacked arrays — `data`, `energies`, `powder_data`, ...
Nothing is stacked on a leading axis, so `batch` cannot read it; keying on
the file would make all 20,000 one entity.

```yaml
key: NIPS3_9DOF
metadata:
  data_type: simulation
  material: NiPS3
  method: RIXS
source:
  groups:
    directory: /sdf/data/lcls/ds/prj/prjmaiqmag01/results/data-source/Zhantao
    file: nips3_fwhm4_9dof_20000_20260303_0537.h5
    pattern: "sample_*"
    params: {group: params, from: datasets}   # read at /sample_N/params
artifacts:
  - {type: spectrum, dataset: data}           # relative: serves /sample_N/data
  - {type: energies, dataset: energies}
  - {type: powder_spectrum, dataset: powder_data}
  - {type: powder_energies, dataset: powder_energies}
  - {type: powder_qs_lab, dataset: powder_qs_lab}
  - {type: qs_lab, dataset: qs_lab}
  - {type: qs_rlu, dataset: qs_rlu}
```

Expected: `entities=20000 artifacts=140000` (entities × 7 artifacts). The file
is opened once for the whole walk. Add `server_base_dir` if the server mounts
these bytes at a different absolute path than this host does — `groups`
accepts it, which is why `table` was never a real option for this dataset.

## What the generated manifest contains

Generation writes `entities.parquet` (uid + one column per parameter [+ extra,
locator columns]) and `artifacts.parquet`
(`uid,type,file,dataset,index,shape,dtype`; empty-but-typed for table
sources). Shape and dtype are captured now; registration never opens HDF5.

## Error triage — symptom, cause, fix

Generation errors are prefixed with the offending filename and the YAML key
involved. Decode the common ones here before changing anything else:

| Symptom | Cause | Fix |
|---|---|---|
| `OSError: ... file signature not found` / `<file>: cannot open as HDF5` | `pattern` matched a non-HDF5 sibling (a `.nc` twin, a Parquet sidecar) | Tighten `pattern` (e.g. `"*.h5"`); re-check `SKILL.md`'s inspect step (`ls -la` the directory) |
| Bare `KeyError: "... object 'X' doesn't exist"` with **no filename** | An HDF5 path is wrong in your own snippet (generation always prefixes the filename: `<file>: <yaml key> '/X' not found in file` means that path is absent in that matched file) | Run `SKILL.md`'s h5py dump on the named file; fix the group/dataset path in the YAML |
| `<file>: no params at group='...' from=...` | **files only.** Wrong `group`, wrong `from` (attrs vs datasets), or the "scalars" are `(1,)`-shaped, which `from: datasets` skips | Check the dump: attrs on the group → `from: attrs`; `shape=()` datasets → `from: datasets`; `(1,)` shapes → unsupported, stop and report; genuinely no params anywhere in the file → `params: null` (explicit opt-in — see `SKILL.md`'s decision table) |
| `<file>: no (N,) datasets under <group>` | **batch only.** The `params.group` holds no 1-D datasets, so there is nothing to key entities by — often the group is wrong, or the params are scalars, which means the dataset is `files`, not `batch` | Re-read the dump: `batch` requires `(N,)` datasets under `group` whose N matches the artifacts' leading axis. Scalar params instead → it is a `files` dataset; go back to `SKILL.md`'s decision table |
| `<file>: param <name> shape (...), expected leading axis N` | **batch only.** One dataset under `params.group` is not axis-0-matched with the entities | That dataset is not a per-entity param. Move it out of `group`, or record it in `metadata` yourself as a shared entry (e.g. `shared_eloss: /eloss`) |
| `sidecar column 'uid' is reserved` | The table sidecar carries a `uid` column — that name is the provenance hash | Rebuild the sidecar with the column renamed (e.g. `producer_uid`) or dropped, point `path` at the rebuilt file |
| `pyarrow.lib.ArrowInvalid` while writing manifests | One param changes type/shape across files (scalar in one file, array or string in another) | Dump two files and diff their params; fix the inconsistent one or report |
| `uid collision: N duplicate provenance ids` | Table `id` column is not unique (all-NaN ids hash identically), or duplicate rows | Choose a genuinely unique id column; dedupe the sidecar |
| `no files match '...' under ...` | Wrong `pattern` — or the `directory` itself does not exist (same message) | `ls` the directory first; test the glob: `ls <directory>/<pattern>` |

