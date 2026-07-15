# FINDINGS — tcb-min ground-up rebuild

Final evidence document. Repo: `/sdf/data/lcls/ds/prj/prjmaiqmag01/results/cwang31/codes/tcb-min`
(sdfiana025, branch master). Server: `tiled serve config config.yml --api-key tcbmin` on
http://127.0.0.1:8017, sqlite `catalog.db`, tiled 0.2.14 (dep pinned `>=0.2.9,<0.3`, resolved and
compatibility-verified in uv.lock). Every number below traces to a committed test, a registration
log line, or a proof-script output quoted in the iteration reports.

## 1. The final minimal file set

| file | LOC | capability that breaks if removed |
|---|---:|---|
| `tcb_min/manifest.py` | 321 | Onboarding: YAML contract -> validated `entities.parquet` + `artifacts.parquet` (uid, shape, dtype baked in). No manifest, nothing to register. |
| `tcb_min/register.py` | 158 | The only catalog write path: manifest -> HTTP `create_container` + `.new()` with external `DataSource` (`application/x-hdf5-broker`). Never opens HDF5. |
| `tcb_min/lazy_hdf5.py` | 149 | Mode-B efficient reads. The stock `HDF5ArrayAdapter` dask-wraps and loads the ENTIRE dataset per request — for BROAD_SIGMA (2000-row stacks) that is gigabytes to serve kilobytes. This adapter reads only the requested bytes (`ds[base_index]` then user slice). |
| `tcb_min/client.py` | 71 | Mode A: `locate(entity)` parses `path_/dataset_/index_<type>` locators; `load(entity, type, base_dir, slc)` does direct h5py — including the batched-index pitfall (row index applied BEFORE the user slice, the line that gets copy-pasted wrong). Everything else is raw `tiled.client`, taught in the module docstring. |
| `tcb_min/__init__.py` | 0 | — |
| **total** | **699** | vs old `src/` ~3,698 LOC |

Two CI budgets keep it minimal (`tests/test_smoke.py`, red CI on violation):
- **LOC budget:** source total <= 700 (`test_loc_budget`; currently 699).
- **Concept budget:** contract top-level keys pinned to exactly `{key, metadata, source, artifacts}`
  (`test_contract_concept_budget`). Contract growth fails a test, not a review.

Non-source: `ONBOARDING.md` (the blind agent's compiler: inspection protocol, decision table,
limits/reserved names, error-triage table), `config.yml`, `datasets/*.yml` (4 corpus YAMLs),
`manifests/`, `tests/` (`test_smoke.py` 6 tests, `verify_live.py`, `proof.py`).

## 2. Dropped from the old ~3,698-LOC broker — "simple but not simpler"

### Cuts

| cut | LOC | replaced by |
|---|---:|---|
| `cli.py` (5-command CLI) | 617 | runnable modules: `python -m tcb_min.manifest`, `python -m tcb_min.register` |
| `bulk_register.py` (SQL route) | 546 | nothing — ADR-0002 already made HTTP the only route; ThreadPool(8) closed the gap |
| `tools/inspect.py` (HDF5 -> draft YAML heuristics) | 853 | executable inspection protocol in ONBOARDING.md: run a dump snippet, read a decision table, predict counts. The agent inspects; no code guesses. |
| `clients/tiled_cache.py` (disk cache + PyTorch Dataset) | 417 | out of scope — client-side training tooling, not broker |
| `tools/schema.py` + `catalog_model.yml` vocab soft-normalization | ~370 | dropped as code; the idea (alias -> canonical id, warn-not-error) is recorded, re-add only when faceted-query fragmentation actually appears |
| `tools/_models.py` pydantic contract | 206 | `validate(raw) -> [error strings]`: explicit checks, ALL errors collected, domain-language messages (`source.files.pattern is 42 (int) — must be a non-empty string`), exit 1 |
| `config.py` env/.env indirection | 113 | two CLI flags (`--url`, `--api-key`) |
| `utils.get_artifact_info` register-time h5py opens | ~70 | shape/dtype are manifest columns captured at generate time — this also fixed the hardcoded-float64 dtype bug (LS `pixel` now serves as int32) |
| `grouped` layout | ~60 | cut — zero real users in the corpus or prod inventory |
| param-hash uid (sorted params, floats rounded 12 dp, json-canonicalized sha256) | ~40 | provenance hash: `sha256(rel_path)` / `sha256("rel:row")` / `sha256(table_id)` — identity is WHERE it came from; collisions impossible instead of silently deduplicated |
| `shared` / `extra_metadata` / `label` contract keys | ~50 | `shared` axes are plain metadata entries the author writes (`shared_eloss: /eloss`); `extra_metadata` -> `batch.extra` (kept: `/log_probs` has a real user); `label` had no renderer |

### Inverse proof — every KEPT thing has a capability that failed (or measurably would) without it

- **`params: null`** (files source, explicit opt-in) exists because blind trial_challenge proved the
  contract hole: `YbBi2IO4.h5` is readable HDF5 with 9 servable arrays and zero scalar params; v2
  hard-errored (`no params at group='/'`), forcing a pointer-only `table` fallback that lost Mode B
  for bytes Tiled could serve. Fix commit 12023aa; CHALLENGE now registers 1/9 with all 9 arrays
  readable over HTTP (verified: `cef_spectrum (264, 101) float64`).
- **Filename-context errors** (`_h5open`/`_h5get` wrappers) exist because the Conway audit (E5)
  traced raw h5py `KeyError`s reaching the agent with no filename at all — fatal across 10,000
  files. Every per-file HDF5 access now reports `<file>: <yaml key> '<path>' not found`.
- **`table.locator` `{col}` templates** kept against the geohot cut because the CNCS sidecar is a
  read-only provider artifact — we cannot demand precomputed URL columns, so the broker renders
  `globus_path`/`globus_url` once at generate time.
- **Idempotent skip + half-registered check**: skip-if-exists is load-bearing for blind-agent
  retries; the geohot audit flagged bare skip as fail-open after a crashed run, so skip now requires
  child-count == manifest artifact count, else loud `WARNING half-registered` counted under `failed`.
- **Parquet seam (generate != register)**: re-register without re-walking Lustre (BROAD_SIGMA walk is
  the filesystem-bound half); the manifest is a complete contract so register never opens HDF5 —
  collapsing the seam is exactly the crack the float64 bug crawled out of; and the parquet is the
  human-auditable artifact between a blind agent's work and the catalog.
- **Mode-A locators** (`path_/dataset_/index_<type>` on entity metadata): half the dual-mode
  promise and the half physicists use for training loops; proven live in L4/L7 and in all three trials.
- **Private mimetype `application/x-hdf5-broker`**: one string that keeps stock
  `application/x-hdf5` dispatch unclobbered for any coexisting vanilla registrations.
- **ThreadPool(8) in register**: the one performance rung, justified by the old repo's measurement
  (~80% wall-clock in `socket.recv`); 10,000 entities in ~10 min.
- **Predict-then-compare + decision table in ONBOARDING.md**: the three blind trials each produced
  exact count predictions; the rixs trial reproduced the legacy 7/42 cold, first pass.

### Deliberately NOT rebuilt

- **`server_base_dir` path mapping.** `register.py` builds the asset URI directly from the YAML
  `source.*.directory` (`file://localhost{directory}/{file}`): authoring host == serving host here
  (everything runs on sdfiana025 against local /sdf paths). This breaks the moment manifests are
  generated on a host whose paths differ from the server's view — e.g. the tiled-test/tiled-dev pods,
  which see project storage under mapped mount points (the old repo carried both YAML
  `server_base_dir` and `TILED_SERVER_PATH_MAP` for exactly this). Re-adding is one optional YAML
  key + one join in `_register_artifact`.
- **`grouped` layout, vocabulary normalization, the CLI, the client cache, inspect.py heuristics** —
  see cuts table; each has a recorded re-entry path.
- **`Regex` queries** — not SQL-served on the catalog in tiled 0.2.x (registered only on MapAdapter);
  the client docstring bans it and teaches `Like` instead. Load-bearing documentation, not code.
- **Parquet schema-level provenance metadata** (generator/config-hash stamps) — no consumer named.

## 3. Retrieval / export decision

**Slicing (Mode B):**
```python
arr = client["BROAD_SIGMA"]["BROAD_SIGMA_4c90dc7a2321a"]["rixs_spectrum"]
a = arr[0:5, :]        # ArrayClient.__getitem__/read -> GET /array/full?slice=0:5,:
```
The server slices before serializing; with the lazy adapter only the requested bytes are read from
disk (`ds[base_index]` row read, then the user slice). The client auto-splits requests over
100 MiB, so the server's 300 MB response cap is never hit. Proven: `(5, 40) float64` (L5), and the
served slice is `numpy.array_equal` to a direct h5py read of the same file+dataset+index (L7).

**Bulk (whole entity, one round trip):**
```python
buf = io.BytesIO()
client["LCLS_RIXS_STATIC"]["LCLS_RIXS_STATIC_a250a4c3e0495"].export(buf, format="application/x-hdf5")
# Container.export -> GET /container/full/{path}?format=application/x-hdf5
```
Single round trip, whole subtree: containers become HDF5 groups, arrays become datasets, entity
metadata lands as root attrs, and no server byte cap is enforced on this route. Verified: a
**370,924-byte** HDF5 blob containing all **9** LS datasets plus the full metadata attr set,
readable via `h5py.File(io.BytesIO(b))` (L6).

**Survey conclusion:** in tiled 0.2.14 there is no better in-Tiled bulk API. `BaseClient.raw_export()`
(`GET /asset/bytes`) streams the original registered file verbatim — the zero-recompression
alternative when artifact == whole file; the websocket stream endpoint is live-update subscription,
not bulk; there is no `.npy` serializer. `container.export(BytesIO(), format="application/x-hdf5")`
is the bulk answer; `arr[slice]` is the slicing answer; Mode A (`client.locate` + h5py) bypasses the
server entirely when you are on the filesystem.

## 4. Proof summary

**Registration counts (live catalog, 7 datasets):**

| dataset | entities / artifacts | inventory known-good | match |
|---|---|---|---|
| LCLS_RIXS_STATIC | 1 / 9 | 1 / 9 | exact |
| BROAD_SIGMA | 10000 / 10000 | 10000 / 10000 | exact |
| CNCS_incident_beam | 100 / 0 | 100 / 0 (pointer-only, by design) | exact |
| CHALLENGE | 1 / 9 | legacy Challenge 1 / 9 | exact (restored by `params: null`) |
| TRIAL_RIXS (blind) | 7 / 42 | legacy RIXS 7 / 42 | exact, cold first pass |
| TRIAL_SEQUOIA_NIPS3_POWDER (blind) | 6 / 0 | legacy 6 / 24 | entities exact; arrays Mode-A-only — contract-forced (NeXus nested-group params), judged defensible |
| TRIAL_CHALLENGE (blind) | 1 / 0 | legacy 1 / 9 | pointer-only fallback that exposed the contract hole; superseded by CHALLENGE 1/9 |

All registrations ended `failed=0`; reruns count `skipped` (idempotent).

**L1–L7 proofs** (`tests/proof.py`, `tests/verify_live.py`, verbatim outputs in iteration 3):
- **L1** root listing -> the registered dataset keys, nothing else.
- **L2** dataset query `Key("data_type") == "simulation"` -> `['BROAD_SIGMA', 'CNCS_incident_beam']` (excludes experimental LS).
- **L3** entity range query `0.04 <= sigma < 0.05` -> **464 hits in 0.075 s** over 10,000 entities (server-side SQL COUNT).
- **L4** artifact lookup: LS `pixel` `(1031,) int32` (manifest dtype honored); CNCS pointer entity has 0 children + rendered `globus_url`; `client.locate()` parses the locators.
- **L5** sliced read `a[0:5, :]` -> `(5, 40) float64`.
- **L6** bulk export -> 370,924-byte HDF5 blob, 9 datasets, metadata as root attrs.
- **L7** Mode-A cross-check: served HTTP slice `numpy.array_equal` to direct h5py on the same file+dataset+index — PASS.

**Blind-trial verdicts** (3 cold agents, ONBOARDING.md + raw data only; independently re-verified by a judge):

| trial | contract check | counts | registration | traversal |
|---|---|---|---|---|
| rixs | PASS | 7/7, 42/42 exact | failed=0 | PASS (query by run_number, sliced read of real bytes) |
| nips3_powder | PASS | 6/6; 0 artifacts contract-forced | failed=0 | PASS (52 params + `hdf5_path` locator; Mode-A read OK) |
| challenge | PASS | 1/1; 0 artifacts — exposed the params-hole | failed=0 | PASS (locators verbatim; Mode-A read OK) |

Judge: "Agentic onboarding works out of the box… the two artifact-count mismatches are not agent
errors — they are the contract's pointer-only escape hatch doing exactly what it was designed to
do." The challenge mismatch was then fixed in code (`params: null`, commit 12023aa) rather than
left as guide prose.

**Timings:** BROAD_SIGMA registration ~10 min wall (613–618 s across two independent runs;
~17 entities/s, 10,000 x 2 POSTs over 8 threads); manifest generation for the same 10,000 entities
~1.7 s; sigma range query 464 hits / **0.075 s**; `tests/test_smoke.py` 6 passed in 0.7 s.

**Commit history of the rebuild:** `01db40a` design -> `1d845d3` v0 (792 LOC) -> `b3f2d33` corpus
registered -> `e65a542` v2 refactor (tagged union, provenance uid, explicit checks, budgets) ->
`5c13e62` proofs -> `3c0de82` Conway hardening -> `12023aa` params:null + CHALLENGE 1/9.
