---
name: tilewright-onboard
description: Onboard a new, structure-unknown scientific dataset into a tilewright Tiled catalog — inspect the data, choose the source archetype (files | batch | table | groups), author and validate the dataset YAML, then generate its Parquet manifest with entity/artifact counts you predicted, and STOP before registration. Use when a dataset directory needs a tilewright manifest and its layout is not yet described (per-file HDF5, batched HDF5, many self-contained groups inside one HDF5, or pointer-only non-HDF5/remote data). Do NOT use for querying, reading, serving, or registering datasets already in the catalog, or for a dataset whose manifest already cleared Gate B. DO use it to re-model a dataset whose manifest is wrong — bad params, wrong counts, wrong source archetype — even though the files exist; a failed Gate B still leaves manifests on disk, and re-modelling is this skill's job, not tilewright-register's.
allowed-tools: Read, Write, Edit, Bash
---

# tilewright-onboard — author a tilewright dataset manifest

Onboard a dataset whose structure is **not** described: inspect it, model it in
one dataset YAML, and generate a Parquet manifest whose counts you predicted
first. You have freedom in *how* you model the data; you have a fixed, tested
*done* — two machine-checkable gates. **Stop after Gate B: do not register,
serve, or query.**

**`${CLAUDE_SKILL_DIR}/reference/onboarding.md`** — "the reference" below — is the
field spec: what every key means, four worked examples, the limits the contract
cannot express, and an error-triage table. This skill is the procedure: what to
observe, what to decide, and what proves you are done. Neither file repeats the
other, so do not look here for a field rule or there for a gate. This skill sends
you to the reference three times: in **setup**, for the `directory` mechanism; at
**step 3**, to author the YAML; and at **Gate A**, to decode a contract error with
its **Error triage** table.

## Setup — on a host that can see the data (e.g. sdfiana025)

Your outputs live in a `.tilewright/` directory **inside the data root** — the
directory that contains the dataset you are onboarding. They do not go in the
code tree:

```
<data root>/                     <- stand here for every command below
├── .tilewright/
│   ├── datasets/<KEY>.yml       <- step 3 writes this
│   └── manifests/<KEY>/         <- step 5 writes this
└── <the actual data files>
```

`<KEY>` is the `key:` value you set inside the dataset YAML (e.g.
`BROAD_SIGMA`). **Name the YAML file and the manifest directory after it,
exactly.** Nothing enforces this — `tilewright register` takes both paths as
explicit arguments and reads the catalog key from inside the YAML — but every
command in **tilewright-register** is written as `.tilewright/datasets/<KEY>.yml`
and `.tilewright/manifests/<KEY>/`, so a mismatch silently makes those
copy-pasted commands point at files that do not exist. The shipped
`examples/datasets/` filenames predate this convention (`broad_sigma.yml` holds
`key: BROAD_SIGMA`); copy their *structure*, not their naming.

```bash
cd <tilewright repo root>        # .../codes/tilewright — once, to build the env
export UV_CACHE_DIR=/sdf/data/lcls/ds/prj/prjmaiqmag01/results/cwang31/.UV_CACHE   # S3DF only — omit elsewhere
uv sync                          # once — creates .venv

cd <data root>                   # then work from here
pwd -P                           # <- the PHYSICAL root; anchor directory: to this
mkdir -p .tilewright/datasets .tilewright/manifests
```

Every command below runs **from the data root**, reaching the CLI in the repo
with `uv run --project <tilewright repo root> ...`. Paths inside the commands
are relative to the data root.

In step 3, write `directory:` as that `pwd -P` value — or a subdirectory of it
if the files live one level down — but never as bare `pwd`. If you reached the
data root through a symlink, `pwd` hands you a logical path that **passes both
gates here and then serves nothing**: nothing opens an asset URI until a read,
so neither gate can see it. That is why this is an instruction and not a check.
The reference's `### source` → `directory` bullet has the mechanism. Writing the
physical path now means the problem never exists. Getting it wrong is expensive
later — fixing `directory:` after registration also requires deleting the
registered dataset, because re-registering never rewrites an existing asset.

## Step 1 — inspect (observe; never guess)

1. `ls -la <dataset_dir>` — note **every** non-HDF5 sibling (`.nc` twins,
   `.parquet` sidecars, `.gz`, subdirs). Your `pattern` must exclude them all.
2. Dump ONE candidate file completely — every dataset's shape/dtype and every
   attribute at every level, groups included:

`h5py` lives in the repo's venv, not in the data root — run the dump through
`--project` or it dies on `ModuleNotFoundError`:

```bash
uv run --project <tilewright repo root> python - <<'EOF'
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
EOF
```

## Step 2 — decide source tag, params, and artifact membership

Read your dump against this table. Exactly one source tag per dataset (a tagged
union): `files` (one matched file = one entity), `batch` (entities stacked on
axis 0 inside each file), `groups` (one matched top-level group inside a single
file = one entity), `table` (a sidecar Parquet's rows *are* the entities;
pointer-only, no served bytes).

| Observation in the dump | source tag | params | artifact membership |
|---|---|---|---|
| Scalar params are **attributes** on one group (often the root) | `files` | `{group: <that group>, from: attrs}` | every non-param dataset is an artifact |
| Scalar params are **0-d datasets** (`shape=()`) under one group | `files` | `{group: <that group>, from: datasets}` | every non-param dataset is an artifact |
| One group holds `(N,)` datasets and the big arrays are `(N, ...)` with the same leading N | `batch` | `{group: <that group>}` | only `(N, ...)` leading-axis datasets can be artifacts; everything else clients need becomes a metadata path entry |
| **One** file holds many sibling top-level groups (`/sample_1`, `/sample_2`, ...), each self-contained: its own params subgroup and its own **unstacked** arrays | `groups` | `{group: <subgroup, relative>, from: attrs\|datasets}` | every non-param dataset **inside a group** is an artifact, named relative to it |
| Readable HDF5, arrays present, but **no scalar params anywhere** (no attrs, no 0-d datasets) | `files` | `null` — explicit opt-in: entities keyed by file path alone, metadata is just `uid` | every dataset is an artifact |
| h5py cannot open the files at all (not HDF5, or data lives at a remote facility), but a per-entity Parquet table exists or can be built | `table` | — (`id` = a unique column) | none: `artifacts` absent or `[]` |
| **Openable** HDF5 but no extractable scalar params at a single group (params scattered in nested subgroups) | prefer `files` + `params: null` if the arrays should be served; `table` if you have (or build) a per-entity sidecar and accept pointer-only | per that choice | per that choice |

**`batch` vs `groups`** — both put many entities in one file; the dump tells them
apart. Stacked on a leading axis (`/spectra` is `(2000, 151)`) → `batch`. Sibling
groups each holding their own unstacked arrays (`/sample_1/data` is `(151,)`, and
there are 2000 such groups) → `groups`. Do **not** reach for `table` here: it
serves no bytes and forbids `server_base_dir`, so it cannot onboard this layout —
it is a pointer-only fallback, not a way to model an openable file.

**Membership rule (binding):** `files` → every non-param dataset is an
artifact. `batch` → only `(N, ...)` leading-axis datasets can be artifacts;
everything else a client needs (e.g. a `(151,)` energy axis) becomes a metadata
path entry you write yourself (`shared_eloss: /eloss`). `groups` → as `files`,
but scoped to one entity group: an artifact `dataset` is written **relative** to
the group (`data`, never `/sample_1/data`).

## Step 3 — author `.tilewright/datasets/<KEY>.yml`

Exactly four top-level keys, no others: `key`, `metadata`, `source`, `artifacts`.
What each one accepts is the reference's job, not this skill's. Open
**`${CLAUDE_SKILL_DIR}/reference/onboarding.md`** now and read three of its
sections:

- **Worked example** for the tag you chose in step 2 — copy it, then adapt it;
- **The dataset YAML contract — field by field** — the `### source` subsection for
  your tag, plus `### artifacts`;
- **Limits and reserved names** — before you invent a key the contract cannot say.

Unknown keys and wrong types fail loudly at Gate A. But Gate A cannot tell you
that a key you never wrote was the one you needed, which is why you read the
section rather than guess and let the gate correct you: the silent losses are the
*optional* keys — `extra`, `locator`, `params: null` — and no gate asks for them.

## Step 4 — Gate A: validate the contract (touches no data)

```bash
uv run --project <tilewright repo root> tilewright manifest .tilewright/datasets/<KEY>.yml --check
```

**Gate A passes only when it prints `contract OK: ...` and exits 0.** On a
contract error it prints *every* problem in domain language and exits 1 — fix
them all (decode with the reference's **Error triage** table) and re-run until clean.

## Step 5 — Gate B: predict, generate, compare

**Predict the counts first**, from your step-1/2 reading:

| source | entities | artifacts |
|---|---|---|
| files | matched files (`ls <directory>/<pattern> \| wc -l`) | entities × len(artifacts) |
| batch | Σ N over matched files (N = leading axis of artifact[0]) | entities × len(artifacts) |
| groups | top-level **groups** in `file` whose name matches `pattern` | entities × len(artifacts) |
| table | sidecar rows | 0 |

Then generate:

```bash
uv run --project <tilewright repo root> tilewright manifest .tilewright/datasets/<KEY>.yml -o .tilewright/manifests/<KEY>
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
arrays (entities keyed by file path, metadata is just `uid`). If you fell back to
it because one openable file held many entities as sibling groups, that is
**`groups`** — it serves them and `table` cannot. Switch, or record
in the YAML `metadata` why pointer-only is the right choice for this dataset.
(This is the one onboarding judgment the hard gates cannot catch: a real blind
trial passed both as pointer-only yet silently lost servable arrays.)

## STOP

Done = Gate A ✅ + Gate B ✅ + soft gate cleared. **Do not run
`tilewright register`** — registration and querying are deterministic,
environment-coupled mechanics outside this skill's scope. They are the
**tilewright-register** skill's job, and it starts where you stop: from
`.tilewright/datasets/<KEY>.yml` and `.tilewright/manifests/<KEY>/`. Report
back: the source tag chosen, the dataset YAML, and the verified
`entities=/artifacts=` counts.
