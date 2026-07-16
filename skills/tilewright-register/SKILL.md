---
name: tilewright-register
description: Register an already-manifested tilewright dataset into a Tiled catalog and prove it serves — lay out .tilewright/config.yml so the data root is its own allowlist, start the server, register the Parquet manifests over HTTP, then read one array back through the server to prove the bytes flow. Use when a dataset already has a validated dataset YAML and manifest (the tilewright-onboard skill's Gate B has passed) and it now needs to be in the catalog and queryable. Do NOT use to author or fix a dataset YAML, to generate manifests, or to onboard a dataset whose structure is not yet described — that is tilewright-onboard.
allowed-tools: Read, Write, Edit, Bash
---

# tilewright-register — put a manifested dataset into the catalog

Take a dataset that already cleared **tilewright-onboard**'s Gate A + Gate B and
make it live: configure, serve, register, and **read one array back through the
server**. Registration never opens HDF5 — so a manifest can register perfectly
and still serve nothing. Only the read-back proves it works. That is why the
last gate is not optional.

**Precondition:** `.tilewright/datasets/<KEY>.yml` and
`.tilewright/manifests/<KEY>/{entities,artifacts}.parquet` both exist. If they
do not, stop and use **tilewright-onboard** first.

## The layout — why there is no allowlist to edit

`.tilewright/` lives **inside the data root**, and the config allowlists
`.tilewright/`'s parent. The data root allowlists *itself*:

```
<data root>/                     <- readable_storage: everything below is servable
├── .tilewright/
│   ├── config.yml               <- the file you write in step 1
│   ├── catalog.db               <- created on first serve
│   ├── datasets/<KEY>.yml       <- from tilewright-onboard
│   └── manifests/<KEY>/         <- entities.parquet + artifacts.parquet
└── <the actual data files>      <- already under readable_storage, by construction
```

Tiled refuses to serve any asset outside `readable_storage` (`Refusing to serve
... because it is outside ...`). Because every dataset you onboard here already
sits under the data root, **a new dataset never needs a config change and never
needs a server restart.** If you find yourself editing an allowlist to admit a
dataset, the `.tilewright/` directory is in the wrong place — it belongs beside
the data, not beside the code.

**Binding rule:** run every command in this skill from the **data root** (the
directory that contains `.tilewright/`). Both `uri` and `readable_storage`
below resolve against the current working directory, so this one rule keeps
them pointing where you think they point. Verify before anything else:

```bash
cd <data root>
ls -d .tilewright          # must print .tilewright
```

Then confirm the layout actually holds — that the data this dataset describes
really is under this root. `source.<tag>.directory` in the dataset YAML is
always an absolute path (the manifest generator rejects a relative one), so
compare it against where you are standing:

```bash
grep -n 'directory:' .tilewright/datasets/<KEY>.yml
pwd
```

The `directory:` value must be the data root or live beneath it. If it points
somewhere else, `.tilewright/` is beside the wrong tree — the registration will
still succeed and the first read will fail (see step 4). Fix it now by moving
`.tilewright/` to the root that actually contains the data.

The tilewright CLI itself is installed elsewhere (the repo). Reach it without
leaving the data root:

```bash
uv run --project <tilewright repo root> tilewright ...
```

## Step 1 — write `.tilewright/config.yml`

Copy this verbatim; it needs no substitution when you are standing in the data
root:

```yaml
uvicorn:
  host: "127.0.0.1"
  port: 8017
trees:
  - path: /
    tree: catalog
    args:
      uri: "sqlite:///.tilewright/catalog.db"
      init_if_not_exists: true
      adapters_by_mimetype:
        application/x-hdf5-broker: "tilewright.lazy_hdf5:LazyHDF5ArrayAdapter"
      readable_storage:
        - "."
```

| Key | Why it is that value |
|---|---|
| `uri` | The catalog DB lives with the data it describes, not in the code tree. Relative → resolved against the data root. |
| `init_if_not_exists` | First serve creates `catalog.db`; there is no separate init step. |
| `adapters_by_mimetype` | Binds the `application/x-hdf5-broker` mimetype the manifests carry to tilewright's lazy reader. Registration writes that mimetype; without this line the server cannot decode it. |
| `readable_storage: ["."]` | `.tilewright/`'s parent — the data root itself. This is the whole point: the allowlist is the data root, so it never changes. |

If you cannot guarantee the server's working directory (a systemd unit, a job
scheduler), replace `"."` with the data root's **absolute** path — same
directory, spelled so cwd cannot move it. Do not replace it with a *narrower*
path; that reintroduces the per-dataset allowlist edit this layout exists to
delete.

## Step 2 — serve (its own terminal; leave it running)

```bash
uv run --project <tilewright repo root> tiled serve config .tilewright/config.yml --api-key tcbmin
```

Serves `http://127.0.0.1:8017` and creates `.tilewright/catalog.db` on first
run. **Gate 1 passes when the catalog answers:**

```bash
curl -s -H "Authorization: Apikey tcbmin" http://127.0.0.1:8017/api/v1/metadata/
```

An HTTP response (not a connection refusal) means the server is up. A restart is
needed only if you edit `config.yml` — which, per the layout above, adding a
dataset never requires.

## Step 3 — Gate 2: register

```bash
uv run --project <tilewright repo root> tilewright register .tilewright/datasets/<KEY>.yml \
    --manifests .tilewright/manifests/<KEY> --url http://localhost:8017 --api-key tcbmin
```

**Gate 2 passes only on `failed=0`:**

```
dataset=<KEY> entities_added=<N> artifacts_added=<M> skipped=0 failed=0
```

**Read the summary line, never the exit code.** `tilewright register` exits 0
even when every row failed: per-entity and per-artifact errors are caught,
printed to stderr as `FAILED ...`, tallied into `failed=`, and then the command
returns 0 anyway. `$?`, `&&`, and `set -e` will all report success on a total
failure. The printed line is the only truth.

`entities_added` + `skipped` must equal the `entities=` count Gate B printed —
anything less means rows silently failed. Re-running is safe: an already
complete entity counts as `skipped`, not an error. A bare `Retrying...` on
stderr from the tiled client is a harmless transient; judge only the summary
line.

## Step 4 — Gate 3: read one array back (the gate that actually proves it)

Registration copies paths and shapes; it never opens the data. Gate 2 can pass
on a dataset the server cannot read a single byte of. Prove the bytes flow:

```python
from tiled.client import from_uri
c = from_uri("http://localhost:8017", api_key="tcbmin")
ent = next(iter(c["<KEY>"]))              # first entity
art = next(iter(c["<KEY>"][ent]))         # first artifact
arr = c["<KEY>"][ent][art][:]             # <- the read-back
print(art, arr.shape, arr.dtype)
```

**Gate 3 passes only when a real array comes back with the shape the manifest
predicted.** For a `table` (pointer-only, 0 artifacts) dataset there is nothing
to read back — Gate 3 is instead: the entity metadata round-trips
(`c["<KEY>"][ent].metadata`) and its locator columns are present. Say so in your
report rather than skipping the gate silently.

## Error triage — symptom, cause, fix

| Symptom | Cause | Fix |
|---|---|---|
| `Refusing to serve <path> because it is outside ...` | The data is not under `readable_storage` — `.tilewright/` is not in the data root, or the server was started from another directory | Do not widen the allowlist to paper over it: move `.tilewright/` beside the data, or restart the server from the data root. This error is the layout telling you it was bypassed |
| `httpx.ConnectError` / connection refused during register | Server not running, or a different port | Step 2 first; confirm the `curl` in Gate 1 answers |
| Register prints `failed=<N>` with a loud WARNING about child count | A crashed earlier run left a half-registered entity | Delete that entity from the catalog and re-register; `skipped` is fine, `failed` is not |
| `catalog.db` appears in the code repo, not beside the data | A command ran from the repo root instead of the data root | Delete the stray DB, `cd` to the data root, re-run. This is the binding rule biting |
| Server starts but `c["<KEY>"]` is a `KeyError` | Registration went to a different catalog than the one being served (two `catalog.db` files) | Confirm both the serve and the register command ran from the same data root |
| Array read returns 500 / adapter error | `adapters_by_mimetype` missing from the config | Restore that block in `.tilewright/config.yml` and restart |

## STOP

Done = Gate 1 ✅ + Gate 2 ✅ + Gate 3 ✅. Report: the `entities_added/skipped/
failed` summary line, and the shape+dtype of the array you read back through the
server. Do not edit the dataset YAML or regenerate manifests here — a contract
or count problem belongs to **tilewright-onboard**; come back when Gate B is
green again.
