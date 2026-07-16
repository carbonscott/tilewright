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
`.tilewright/manifests/<KEY>/{entities,artifacts}.parquet` both exist, where
`<KEY>` is the `key:` value inside that YAML — the file and the manifest
directory are both named after it. If they do not exist, stop and use
**tilewright-onboard** first.

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

Tiled serves an asset only if its path is under `readable_storage`. Because
every dataset you onboard here already sits under the data root, **a new
dataset never needs a config change and never needs a server restart.** If you
find yourself editing an allowlist to admit a dataset, the `.tilewright/`
directory is in the wrong place — it belongs beside the data, not beside the
code.

One data root = one config = one catalog = **one server on its own port**.

**Binding rule:** run every command in this skill from the **data root** (the
directory that contains `.tilewright/`). Both `uri` and `readable_storage`
below resolve against the current working directory, so this one rule keeps
them pointing where you think they point.

```bash
cd <data root>
ls -d .tilewright          # must print .tilewright
```

Then confirm the layout actually holds — that the data really is under this
root, **compared as physical paths**. The allowlist check is pure string
prefixing and does not resolve symlinks, while the server's idea of "here" is
always physical. A logical `/sdf/...` path that symlinks elsewhere passes this
eyeball test and fails at read:

```bash
grep -n 'directory:' .tilewright/datasets/<KEY>.yml
readlink -f "$(grep -m1 'directory:' .tilewright/datasets/<KEY>.yml | awk '{print $2}')"
pwd -P                     # the physical data root
```

The resolved `directory:` must equal `pwd -P` or sit beneath it. If it does
not, either `.tilewright/` is beside the wrong tree (move it), or `directory:`
is a logical path through a symlink (make it physical — see triage). Do not
skip this: registration will succeed either way, and only the first read fails.
(A `table` source registers no assets at all, so nothing can be refused — this
check has no consequence there.)

The tilewright CLI is installed in the repo, not here. Reach it without leaving
the data root — `--project` selects the environment and does **not** change the
working directory:

```bash
export UV_CACHE_DIR=/sdf/data/lcls/ds/prj/prjmaiqmag01/results/cwang31/.UV_CACHE
uv run --project <tilewright repo root> tilewright ...
```

## Step 1 — write `.tilewright/config.yml`

Copy this; the one value to consider changing is the port:

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
| `adapters_by_mimetype` | Binds the `application/x-hdf5-broker` mimetype the manifests carry to tilewright's lazy reader. **Without this block, registration itself fails with 415** — it is not optional and not read-time-only. |
| `readable_storage: ["."]` | `.tilewright/`'s parent — the data root itself. This is the whole point: the allowlist is the data root, so it never changes. |
| `uvicorn.port` | **Give each data root its own port.** This layout is one catalog per data root, so two data roots both defaulting to 8017 collide — and the collision is silent and dangerous (see Gate 1). |

If you cannot guarantee the server's working directory (a systemd unit, a job
scheduler), replace `"."` with the data root's **physical absolute** path
**and** make `uri` absolute too (`sqlite:////abs/path/.tilewright/catalog.db`).
Both are cwd-relative; absolutizing only one silently creates a second, empty
`catalog.db` wherever the unit happens to run. Do not replace `"."` with a
*narrower* path — that reintroduces the per-dataset allowlist edit this layout
exists to delete.

## Step 2 — serve (its own terminal; leave it running)

```bash
uv run --project <tilewright repo root> tiled serve config .tilewright/config.yml --api-key tcbmin 2>&1 | tee .tilewright/server.log
```

Creates `.tilewright/catalog.db` on first run.

### Gate 1 — the server answering is *the one you started*

A curl that gets a reply proves *a* server is up, not *your* server. If a stale
server from another data root already owns the port, yours exits with `address
already in use`, the curl below answers from the impostor, registration writes
into **its** catalog, and Gate 3 reads back somebody else's array — all three
gates green, nothing registered where you intended. Check your own log first:

```bash
grep -q "address already in use" .tilewright/server.log && echo "PORT TAKEN — pick another in config.yml"
grep -q "Application startup complete" .tilewright/server.log && echo "my server is up"
curl -s -H "Authorization: Apikey tcbmin" http://127.0.0.1:8017/api/v1/metadata/ | head -c 80
```

**Gate 1 passes only when your own log shows `Application startup complete`,
shows no `address already in use`, and the curl answers.** A restart is needed
only if you edit `config.yml` — which, per the layout above, adding a dataset
never requires.

## Step 3 — Gate 2: register

```bash
uv run --project <tilewright repo root> tilewright register .tilewright/datasets/<KEY>.yml \
    --manifests .tilewright/manifests/<KEY> --url http://localhost:8017 --api-key tcbmin
```

```
dataset=<KEY> entities_added=<N> artifacts_added=<M> skipped=0 failed=0
```

**Gate 2 passes only when, against a fresh catalog, all three hold:**

1. `failed=0`,
2. `entities_added` + `skipped` equals Gate B's `entities=` count, **and**
3. `artifacts_added` equals Gate B's `artifacts=` count.

Check 3 is not redundant. Entity and artifact failures land in the same
`failed` tally, so the entity arithmetic can look perfect while every artifact
broke.

**Read the summary line, never the exit code.** `tilewright register` exits 0
even when every row failed: errors are caught, printed to stderr as `FAILED
...`, tallied into `failed=`, and then the command returns 0 anyway. `$?`,
`&&`, and `set -e` will all report success on a total failure.

**`skipped` does not mean "complete".** Re-running is only safe after a *clean*
run. An entity is counted `skipped` when its child *count* matches the
manifest — the children are never inspected. A previous run that failed
mid-artifact leaves committed-but-empty array children, so a re-run reports
`entities_added=0 skipped=N failed=0` — Gate 2 green — on a catalog that 500s
on every read. **After any run with `failed>0`, delete the dataset container
before re-registering:**

```bash
uv run --project <tilewright repo root> python -c "
from tiled.client import from_uri
c = from_uri('http://localhost:8017', api_key='tcbmin')
c['<KEY>'].delete(recursive=True)   # NOT del c['<KEY>'] — containers reject item deletion
print('deleted <KEY>; catalog keys now:', list(c))
"
```

A bare `Retrying...` on stderr from the tiled client is a harmless transient;
judge only the summary line.

## Step 4 — Gate 3: read one array back (the gate that actually proves it)

Registration copies paths and shapes; it never opens the data. Gate 2 can pass
on a dataset the server cannot read a single byte of. Prove the bytes flow:

```bash
uv run --project <tilewright repo root> python -c "
from tiled.client import from_uri
c = from_uri('http://localhost:8017', api_key='tcbmin')
ent = next(iter(c['<KEY>']))              # first entity
art = next(iter(c['<KEY>'][ent]))         # first artifact
arr = c['<KEY>'][ent][art][:]             # <- the read-back
print(art, arr.shape, arr.dtype)
"
```

**Gate 3 passes only when a real array comes back with the shape the manifest
predicted.** For a `table` (pointer-only, 0 artifacts) dataset there is nothing
to read back — it registers no assets at all, so no read can ever fail. Gate 3
is instead: the entity metadata round-trips (`c["<KEY>"][ent].metadata`) and
its locator columns are present. Say so in your report rather than skipping the
gate silently.

## Error triage — symptom, cause, fix

Gate 3 failures surface client-side as a bare **500**; the explanatory line is
in the **server's** terminal/log, not in your client output. Read
`.tilewright/server.log` before matching a row below.

| Symptom | Cause | Fix |
|---|---|---|
| Gate 2 prints `FAILED artifact ...: 415: The given data source mimetype, application/x-hdf5-broker, is not one that the Tiled server knows how to read` | `adapters_by_mimetype` missing from `.tilewright/config.yml` — this fails at **registration**, not at read | Restore that block, restart the server, then **delete the dataset container (`c['<KEY>'].delete(recursive=True)`) and re-register** — the failed run left empty children that a plain re-run would count as `skipped`, hiding the breakage behind a green Gate 2 |
| Gate 3 returns 500, and the server log says `Refusing to serve file://localhost/<path> because it is outside the readable storage area for this server` | The data is not under `readable_storage` — `.tilewright/` is not in the data root, or the server was started from another directory | Do not widen the allowlist to paper over it: move `.tilewright/` beside the data, or re-run the serve command from the data root so the allowlist means what it says. This error is the layout telling you it was bypassed |
| Same `Refusing to serve` for data that IS under the root | Symlinked root: `readable_storage: ["."]` resolves to the **physical** cwd, `directory:` is a **logical** path, and the check is string-prefix only — it never resolves symlinks | Make `directory:` physical (`readlink -f`) in the dataset YAML and regenerate, or set `readable_storage` to the physical absolute path. Compare `readlink -f <directory>` against `pwd -P` |
| Serve exits: `[Errno 98] error while attempting to bind on address ('127.0.0.1', 8017): address already in use` | Another data root's server already holds the port — this layout is one catalog per data root | Pick a free `uvicorn.port` in `.tilewright/config.yml` and pass the matching `--url http://localhost:<port>` to register. Do **not** merge two data roots into one catalog to dodge it |
| `httpx.ConnectError` / connection refused during register | Server not running, or a different port | Step 2 first; confirm Gate 1 passes |
| Register prints `failed=<N>` with a loud WARNING about child count | A crashed earlier run left a half-registered entity | Delete the dataset container (see Gate 2) and re-register; `skipped` is fine only after a clean run, `failed` never is |
| `catalog.db` appears in the code repo, not beside the data | A command ran from the repo root instead of the data root | Delete the stray DB, `cd` to the data root, re-run. This is the binding rule biting |
| Server starts but `c["<KEY>"]` is a `KeyError` | The server answering `--url` is not the one you started — a leftover on the same port, or a server launched from a different cwd (register reaches the catalog only over HTTP; its own cwd cannot pick a catalog) | Confirm via `.tilewright/server.log` that your server owns the port (Gate 1), then re-register |

## STOP

Done = Gate 1 ✅ + Gate 2 ✅ + Gate 3 ✅. Report: the `entities_added/skipped/
failed` summary line, and the shape+dtype of the array you read back through the
server. Do not edit the dataset YAML or regenerate manifests here — a contract
or count problem belongs to **tilewright-onboard**; come back when Gate B is
green again.
