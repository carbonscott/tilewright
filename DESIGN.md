# tcb-min — design (2026-07-15)

Ground-up, physicist-minimal Tiled catalog broker. Target: **5 source files** (hard cap 7).
Environment: tiled **0.2.9**, Python 3.12, sdfiana025. Own uv project; own isolated sqlite
catalog + server (localhost:8017) — never touches tiled-test/tiled-dev.

## Hierarchy (unchanged from proven model)

Dataset (container, provenance metadata) → Entity (container, physics params as queryable
metadata) → Artifact (array child, external HDF5 referenced in place).

## File set — each earns its place

| file | capability that breaks without it |
|---|---|
| `tcb_min/manifest.py` | Onboarding: YAML contract validation + walks data per layout → `entities.parquet` + `artifacts.parquet`. Runnable: `python -m tcb_min.manifest <yaml> -o manifests/<KEY>/` |
| `tcb_min/register.py` | Registration: manifests → HTTP `create_container`/`.new()` with `DataSource(management=external)`. Runnable module; prints per-dataset entity/artifact counts. |
| `tcb_min/lazy_hdf5.py` | Efficient Mode-B reads: stock `HDF5ArrayAdapter` dask-loads the ENTIRE dataset per request; this adapter reads only the requested slice. `from_catalog(data_source, node, /, dataset=None, slice=None)`. |
| `tcb_min/client.py` | Physicist query/retrieval surface: `connect`, `datasets`, `find` (param-range search), `locate` (Mode-A/globus locators), `fetch` (sliced read), `export_entity` (bulk HDF5 blob). |
| *(reserve: 1–2 more if a capability demands it — justify or stay at 4+0)* | |

Non-source: `pyproject.toml`, `config.yml` (serve config), `ONBOARDING.md` (the guide blind
agents get), `datasets/*.yml`, `manifests/`, `FINDINGS.md`, `tests/`.

## Contract (dataset YAML) — explicit, zero heuristics (ADR-0001 spirit)

```yaml
key: LCLS_RIXS_STATIC          # required, stamped by author
label: "LCLS RIXS Static"
metadata:                       # data_type required; everything else open (extra allowed)
  data_type: experimental
  material: NiPS3
data:
  directory: /abs/path
  layout: per_entity            # per_entity | batched | pointer   (grouped DROPPED: zero real users)
  file_pattern: "*.h5"          # per_entity/batched only
  sidecar: CNCS_srtd.parquet    # pointer only: one row per entity
parameters:
  location: root_attributes     # root_scalars | root_attributes | group | sidecar
  group: /params                # required iff location == group
artifacts:                      # min 1 unless layout == pointer (then may be empty)
  - {type: rixs_spectrum, dataset: /spectra}
shared:                         # optional; surfaced as shared_dataset_<type> on dataset metadata
  - {type: eloss, dataset: /eloss}
extra_metadata:                 # optional; per-entity values excluded from uid hash
  - dataset: /log_probs
locator:                        # pointer only: explicit templates, {col} interpolated per sidecar row
  globus_path: "/maiqmag/.../cncs_new/{filename}"
```

## Manifest contract (Parquet)

- `entities.parquet`: `uid` (sha256(json({ns:key, params:sorted-rounded-12dp}))[:16]) + one
  column per param + extra_metadata cols + (pointer) locator cols.
- `artifacts.parquet`: `uid, type, file (rel to directory), dataset, index (batched row or
  None), shape (json str), dtype (str), file_size, file_mtime`.
  **NEW vs old broker: shape+dtype captured at generate time** → register.py never opens
  HDF5 and dtypes are correct (old code hardcoded float64; `pixel` is int32).
- Pointer datasets: empty artifacts.parquet with the standard columns.

## Registration (tiled 0.2.9, verified API)

- `client.create_container(key, metadata=...)` for dataset + entity; idempotent skip if key exists.
- Entity metadata = all entity-manifest columns + `path_<type>`/`dataset_<type>`/`index_<type>`
  Mode-A locators per artifact.
- Artifact: `entity.new(StructureFamily.array, [DataSource(mimetype="application/x-hdf5-broker",
  structure=ArrayStructure(data_type=BuiltinDtype.from_numpy_dtype(dtype), shape, chunks),
  parameters={"dataset": ..., "slice": str(index)?}, management=Management.external,
  assets=[Asset(data_uri="file://localhost/abs...", parameter="data_uris")])], key=type)`.
- ThreadPoolExecutor(max_workers=8) per entity (proven ~80% wall-clock in socket.recv).

## Serving

`tiled serve config config.yml --api-key <k>`; config: `trees: [{path: /, tree: catalog,
args: {uri: sqlite:///catalog.db, init_if_not_exists: true,
adapters_by_mimetype: {application/x-hdf5-broker: "tcb_min.lazy_hdf5:LazyHDF5ArrayAdapter"},
readable_storage: [<data roots>]}}]`. readable_storage is enforced per asset.

## Query (must be SQL-served)

`tiled.queries.Key("sigma") >= 0.04` → `Comparison` → SQL on nodes.metadata_ JSON
(`tiled/catalog/adapter.py:1566 binary_op`). `Regex` is NOT SQL-backed in 0.2.9 — do not use.
`Container.distinct(...)` for facets.

## Bulk retrieval

- Slice: `arr[0:5, :]` / `ArrayClient.read(slice)` → `GET /array/full?slice=` (server slices
  before serializing; client auto-splits >100 MiB).
- Bulk blob: `container.export(io.BytesIO(), format="application/x-hdf5")` — single round trip,
  whole subtree, metadata→HDF5 attrs, no server byte cap. (Alternative for whole-file
  artifacts: `raw_export` streams original bytes.)

## Proof corpus (from 2026-07-14 inventory)

| dataset | layout | params | expect |
|---|---|---|---|
| LCLS_RIXS_STATIC | per_entity | root_attributes | 1 entity / 9 artifacts |
| BROAD_SIGMA | batched | group (/params) | 10,000 / 10,000 |
| CNCS_incident_beam | pointer | sidecar | 100 / 0 |
| (blind trials) nips3_powder etc. | per_entity | group attrs | 6 / 24 |

## Cuts vs old src/ (~3,698 LOC) — to defend in FINDINGS.md

grouped layout (no users) · tcb CLI 617 LOC (runnable modules instead) · bulk_register (ADR-0002)
· vocab/catalog_model.yml soft-normalization layer (ADR-0003 preserved as idea, dropped as code)
· inspect.py heuristics (already dead) · tiled_cache.py · config.py env indirection ·
utils.get_artifact_info register-time h5py (moved to generate time).


## v2 (2026-07-15) — post-critique refactor

Integrated persona critique (karpathy + geohot) applied. Where the sections
above disagree with this list, **v2 wins**.

1. **Contract is a tagged union.** Top-level keys: `key, metadata, source,
   artifacts` — nothing else (`label`, `shared`, `extra_metadata`,
   `provenance`, `locator`, `data`, `parameters` are gone as top-level
   concepts). `source` holds exactly one of `files | batch | table`, each
   owning only its keys — illegal layout×location combos are
   unrepresentable, not validated away. The old `location` enum split into
   two orthogonal bits: `params: {group, from: attrs|datasets}` (files);
   batch params are always datasets under `params.group`. `batch.extra`
   keeps the `/log_probs` use-case. `table` (ex-pointer) is manifest
   passthrough: sidecar rows ARE the entities; requires `id` (unique
   column); optional `locator` `{col}` templates are KEPT (provider
   sidecars are read-only here — we can't demand precomputed columns).
   Shared axes are plain `metadata:` entries the author writes.
2. **No pydantic.** Explicit checks in manifest.py; ALL errors collected
   then printed in domain language, exit(1); no type coercion.
3. **uid = provenance hash**, not param hash: files → sha256(rel_path);
   batch → sha256("rel_path:row"); table → sha256(str(row[id])); [:16].
   Rounding/sorting/canonicalization machinery deleted. Identical params in
   two files = two entities (correct). Entity key stays `{key}_{uid[:13]}`.
4. **Artifact manifest columns**: `uid,type,file,dataset,index,shape,dtype`
   — `file_size`/`file_mtime` CUT (staleness is caught at read time by the
   adapter's shape/dtype validation, the check that actually fires).
5. **register.py**: mechanism unchanged (external DataSource, broker
   mimetype, ThreadPool 8, `path_/dataset_/index_<type>` Mode-A locators).
   Half-registered entities are no longer silently skipped: existing key →
   compare array-children count to the manifest's; mismatch → loud warning
   + counted under failed.
6. **client.py is Mode A only**: `locate()` + `load()` (batched-index-aware
   direct h5py). The wrapper verbs (`connect/datasets/find/fetch/
   export_entity`) are deleted; the module docstring teaches raw tiled
   (`from_uri`, `Key` search, slicing, export) instead of aliasing it.
7. **Budgets are tests**: `tests/test_smoke.py` runs offline — proof-corpus
   counts as a fixture table, total tcb_min/*.py LOC ≤ 700, contract
   top-level key set frozen at 4. `tests/verify_live.py` (manual) checks a
   served slice against direct h5py — the reference implementation.
8. **pyproject**: pydantic dependency removed.
