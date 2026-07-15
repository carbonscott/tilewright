---
name: tcb-onboard
description: Onboard a new, structure-unknown scientific dataset into a tcb-min Tiled catalog — inspect the data, choose the source archetype (files | batch | table), author and validate the dataset YAML, then generate its Parquet manifest with entity/artifact counts you predicted, and STOP before registration. Use when a dataset directory needs a tcb-min manifest and its layout is not yet described (per-file HDF5, batched HDF5, or pointer-only non-HDF5/remote data). Do NOT use for querying, reading, serving, or registering datasets already in the catalog, or for a dataset that already has a manifest.
allowed-tools: Read, Write, Edit, Bash
---

# tcb-onboard — author a tcb-min dataset manifest

Onboard a dataset whose structure is **not** described: inspect it, model it in
one dataset YAML, and generate a Parquet manifest whose counts you predicted
first. You have freedom in *how* you model the data; you have a fixed, tested
*done* — two machine-checkable gates. **Stop after Gate B: do not register,
serve, or query.**

The full contract (every field), the three worked examples, the limits &
reserved-names list, and the error-triage table live in
**`${CLAUDE_SKILL_DIR}/reference/onboarding.md`** — read it before you write any
YAML (step 3), and again to decode any generator error. This skill is the
procedure; that file is the reference.

## Setup — on a host that can see the data (e.g. sdfiana025)

```bash
cd <tcb-min repo root>        # .../codes/tcb-min
export UV_CACHE_DIR=/sdf/data/lcls/ds/prj/prjmaiqmag01/results/cwang31/.UV_CACHE
uv sync                       # once — creates .venv
```

Every command below is `uv run ...` from the repo root.

## Step 1 — inspect (observe; never guess)

1. `ls -la <dataset_dir>` — note **every** non-HDF5 sibling (`.nc` twins,
   `.parquet` sidecars, `.gz`, subdirs). Your `pattern` must exclude them all.
2. Dump ONE candidate file completely — every dataset's shape/dtype and every
   attribute at every level, groups included:

```python
import h5py
fp = "<one_matched_file>"
with h5py.File(fp, "r") as f:
    for k, v in f.attrs.items():                 # root attrs (visititems skips "/")
        print(f"attr /@{k} = {v!r}")
    def dump(name, obj):
        if isinstance(obj, h5py.Dataset):
            print(f"D /{name}  shape={obj.shape} dtype={obj.dtype}")
        else:
            print(f"G /{name}/")
        for k, v in obj.attrs.items():
            print(f"attr /{name}@{k} = {v!r}")
    f.visititems(dump)
```

## Step 2 — decide source tag, params, and artifact membership

Read your dump against this table. Exactly one source tag per dataset (a tagged
union): `files` (one matched file = one entity), `batch` (entities stacked on
axis 0 inside each file), `table` (a sidecar Parquet's rows *are* the entities;
pointer-only, no served bytes).

| Observation in the dump | source tag | params | artifact membership |
|---|---|---|---|
| Scalar params are **attributes** on one group (often the root) | `files` | `{group: <that group>, from: attrs}` | every non-param dataset is an artifact |
| Scalar params are **0-d datasets** (`shape=()`) under one group | `files` | `{group: <that group>, from: datasets}` | every non-param dataset is an artifact |
| One group holds `(N,)` datasets and the big arrays are `(N, ...)` with the same leading N | `batch` | `{group: <that group>}` | only `(N, ...)` leading-axis datasets can be artifacts; everything else clients need becomes a metadata path entry |
| Readable HDF5, arrays present, but **no scalar params anywhere** (no attrs, no 0-d datasets) | `files` | `null` — explicit opt-in: entities keyed by file path alone, metadata is just `uid` | every dataset is an artifact |
| h5py cannot open the files at all (not HDF5, or data lives at a remote facility), but a per-entity Parquet table exists or can be built | `table` | — (`id` = a unique column) | none: `artifacts` absent or `[]` |
| **Openable** HDF5 but no scalar params at a single group (params scattered in nested subgroups) | prefer `files` + `params: null` if the arrays should be served; `table` if you have (or build) a per-entity sidecar and accept pointer-only | per that choice | per that choice |

**Membership rule (binding):** `files` → every non-param dataset is an
artifact. `batch` → only `(N, ...)` leading-axis datasets can be artifacts;
everything else a client needs (e.g. a `(151,)` energy axis) becomes a metadata
path entry you write yourself (`shared_eloss: /eloss`).

## Step 3 — author `examples/datasets/<KEY>.yml`

Exactly four top-level keys, no others: `key`, `metadata` (`data_type`
required), `source` (exactly one of `files | batch | table`), `artifacts`
(min 1 for files/batch; **absent or `[]` for table**). The field-by-field spec,
the three worked examples, and the limits/reserved-names section are in the
reference doc — copy the closest worked example and adapt it. Unknown keys and
wrong types fail loudly at Gate A.

## Step 4 — Gate A: validate the contract (touches no data)

```bash
uv run tcb manifest examples/datasets/<KEY>.yml --check
```

**Gate A passes only when it prints `contract OK: ...` and exits 0.** On a
contract error it prints *every* problem in domain language and exits 1 — fix
them all (decode with the reference's error-triage table) and re-run until clean.

## Step 5 — Gate B: predict, generate, compare

**Predict the counts first**, from your step-1/2 reading:

| source | entities | artifacts |
|---|---|---|
| files | matched files (`ls <directory>/<pattern> \| wc -l`) | entities × len(artifacts) |
| batch | Σ N over matched files (N = leading axis of artifact[0]) | entities × len(artifacts) |
| table | sidecar rows | 0 |

Then generate:

```bash
uv run tcb manifest examples/datasets/<KEY>.yml -o examples/manifests/<KEY>
```

**Gate B passes only when the summary line
`dataset=<KEY> entities=<N> artifacts=<M>` equals your prediction exactly.**
If it differs, **STOP** — do not proceed. A mismatch means you misread the data
(unmatched files, wrong N, unexpected rows): return to steps 1–3, find the
discrepancy, fix the YAML, and regenerate.

## Soft gate — did you route Tiled-readable bytes to a pointer?

Gates A+B prove the manifest is self-consistent, not that the modeling is
*best*. Before you stop, one judgment check:

**If you chose `table` (pointer-only, 0 artifacts) for data whose arrays h5py
CAN open, justify it in one line or reconsider.** `table` is correct only when
Tiled cannot read the bytes at all (non-HDF5, or remote-facility data), OR you
deliberately accept pointer-only to keep scattered/nested params as queryable
metadata. If you fell back to `table` merely because the scalar params were not
at a single group, prefer **`files` + `params: null`** — that still serves the
arrays (entities keyed by file path, metadata is just `uid`). Switch, or record
in the YAML `metadata` why pointer-only is the right choice for this dataset.
(This is the one onboarding judgment the hard gates cannot catch: a real blind
trial passed both as pointer-only yet silently lost servable arrays.)

## STOP

Done = Gate A ✅ + Gate B ✅ + soft gate cleared. **Do not run
`tcb register`** — registration, serving, and querying are deterministic,
environment-coupled mechanics outside this skill's scope. Report back: the
source tag chosen, the dataset YAML, and the verified `entities=/artifacts=`
counts.
