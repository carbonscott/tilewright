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

Then confirm the layout actually holds. Compare exactly the two strings the
server will compare — and **do not resolve the YAML's path before comparing**,
or you destroy the very signal you are looking for:

- The asset URI is built from the **raw, unresolved** `directory:` string
  (`file://localhost{directory}/{file}`).
- `readable_storage: ["."]` becomes the **physical** cwd — `pwd -P`.
- The allowlist test is a path-*component* containment test
  (`os.path.commonpath`) that never resolves symlinks. It is not a string
  prefix: an allowlisted `/data` does **not** admit `/data-backup`.

```bash
grep -m1 'directory:' .tilewright/datasets/<KEY>.yml   # the RAW string — what the URI uses
pwd -P                                                 # what readable_storage "." becomes
```

**The raw `directory:` must equal `pwd -P` or sit literally beneath it.** If it
does not, one of two things is true:

- it points at an unrelated tree → `.tilewright/` is beside the wrong data;
  move it;
- `readlink -f <directory>` *does* land under `pwd -P` → it is a logical path
  through a symlink. It will be refused anyway, because the test compares the
  paths as written and never resolves them. Rewrite `directory:` as the
  physical path and regenerate the manifest.

Do not skip this: registration succeeds either way, and only the first read
fails. (A `table` source registers no assets at all, so nothing can be
refused — this check has no consequence there.)

The tilewright CLI is installed in the repo, not here. Reach it without leaving
the data root — `--project` selects the environment and does **not** change the
working directory:

```bash
export UV_CACHE_DIR=/sdf/data/lcls/ds/prj/prjmaiqmag01/results/cwang31/.UV_CACHE   # S3DF only — omit elsewhere
uv run --project <tilewright repo root> tilewright ...
```

## Step 1 — write `.tilewright/config.yml`

**Skip this step if `.tilewright/config.yml` already exists** — a second
dataset in the same data root reuses the root's config untouched. That is the
whole promise of the layout: no config edit, no restart.

If a server is already running for this root, skip the serve *command* in step
2 — but **still run Gate 1**. Do not skip to step 3. "The server is already
running" is exactly the belief Gate 1 exists to test, and an impostor on the
port is *most* likely here, on the second dataset, when you did not start the
server in this session and never saw its log.

Otherwise copy this and substitute `<PORT>` — one port per data root, since
each root gets its own catalog and its own server. Everything else is verbatim:

```yaml
uvicorn:
  host: "127.0.0.1"
  port: <PORT>            # 8017 if this is your only data root
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

## Step 2 — serve (background it; it must outlive this step)

The server has to keep running while you register, so start it detached and
keep its PID. A foreground `tiled serve` blocks until you kill it — there is no
second terminal here.

```bash
nohup uv run --project <tilewright repo root> tiled serve config .tilewright/config.yml \
    --api-key tcbmin > .tilewright/server.log 2>&1 &
SERVER_PID=$!
```

Creates `.tilewright/catalog.db` on first run.

### Gate 1 — the server answering is *the one you started*

A curl that gets a reply proves *a* server is up, not *your* server. If a stale
server from another data root already owns the port, yours dies on `address
already in use`, the curl answers from the impostor, registration writes into
**its** catalog, and Gate 3 reads back somebody else's array — all three gates
green, nothing registered where you intended.

Two signals lie here, and you must not build the gate on either:

- **`Application startup complete` is printed *before* the bind is attempted.**
  It appears in a healthy log and in a collided one alike — measured 1 and 1.
  It tells you nothing.
- **A curl answering tells you nothing either** — an impostor on the port
  answers exactly like your server.

The discriminator is **`Uvicorn running on`**, which is printed only after a
successful bind (measured: 1 healthy, 0 collided). And you must **wait** for
the outcome: grepping immediately races the bind and reports "clear" before
uvicorn has even tried. Poll until the log is decisive:

```bash
for i in $(seq 60); do
  grep -qE "Uvicorn running on|address already in use" .tilewright/server.log && break
  sleep 1
done

if grep -q "address already in use" .tilewright/server.log; then
  echo "Gate 1 FAIL — port taken; your server is NOT running. Change uvicorn.port and re-serve"
elif grep -q "Uvicorn running on" .tilewright/server.log && kill -0 $SERVER_PID 2>/dev/null; then
  echo "Gate 1 PASS — your server owns <PORT>"
else
  echo "Gate 1 FAIL — server died; read .tilewright/server.log"
fi
```

**Gate 1 passes only when your own log shows `Uvicorn running on`, shows no
`address already in use`, and `$SERVER_PID` is alive.** If the port was taken,
nothing you do afterwards touches your own catalog — and note
`init_if_not_exists` creates `.tilewright/catalog.db` *before* the bind, so the
DB existing is not evidence your server ever started. A restart is needed only
if you edit `config.yml` — which, per the layout above, adding a dataset never
requires.

## Step 3 — Gate 2: register

```bash
uv run --project <tilewright repo root> tilewright register .tilewright/datasets/<KEY>.yml \
    --manifests .tilewright/manifests/<KEY> --url http://localhost:<PORT> --api-key tcbmin
```

```
dataset=<KEY> entities_added=<N> artifacts_added=<M> skipped=0 failed=0
```

**On a first registration — a fresh catalog, or one you just deleted the
container from — Gate 2 passes only when all three hold:**

1. `failed=0`,
2. `entities_added` + `skipped` equals Gate B's `entities=` count, **and**
3. `artifacts_added` equals Gate B's `artifacts=` count.

Check 3 is not redundant. Entity and artifact failures land in the same
`failed` tally, so the entity arithmetic can look perfect while every artifact
broke.

On a *deliberate re-run* of an already-complete dataset, expect
`entities_added=0 artifacts_added=0 skipped=<N> failed=0` — check 3 does not
apply, and nothing in this summary can distinguish "already complete" from
"silently broken". Only Gate 3 can. Re-run means re-read.

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
c = from_uri('http://localhost:<PORT>', api_key='tcbmin')
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
c = from_uri('http://localhost:<PORT>', api_key='tcbmin')
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

This gate reads **one** artifact of one entity. That is enough to prove the
config, the adapter, and the allowlist all work — the failures this skill is
about are per-dataset, not per-file — but it is not a survey of every file. A
single unreadable file among thousands will not surface here.

## Error triage — symptom, cause, fix

Gate 3 failures surface client-side as a bare **500**; the explanatory line is
in the **server's** terminal/log, not in your client output. Read
`.tilewright/server.log` before matching a row below.

| Symptom | Cause | Fix |
|---|---|---|
| Gate 2 prints `FAILED artifact ...: 415: The given data source mimetype, application/x-hdf5-broker, is not one that the Tiled server knows how to read` | `adapters_by_mimetype` missing from `.tilewright/config.yml` — this fails at **registration**, not at read | Restore that block, restart the server, then **delete the dataset container (`c['<KEY>'].delete(recursive=True)`) and re-register** — the failed run left empty children that a plain re-run would count as `skipped`, hiding the breakage behind a green Gate 2 |
| Gate 3 returns 500, and the server log says `Refusing to serve file://localhost/<path> because it is outside the readable storage area for this server` | The data is not under `readable_storage` — `.tilewright/` is not in the data root, or the server was started from another directory | Do not widen the allowlist to paper over it: move `.tilewright/` beside the data, or re-run the serve command from the data root so the allowlist means what it says. This error is the layout telling you it was bypassed |
| Same `Refusing to serve` for data that IS under the root | Symlinked root: `readable_storage: ["."]` becomes the **physical** cwd, `directory:` is a **logical** path, and the containment test never resolves either | Make the two agree *as written*. Either rewrite `directory:` physically (`readlink -f`) and regenerate the manifest — preferred — or set `readable_storage` to the **same logical path** `directory:` uses (an explicit path is stored unresolved). Setting `readable_storage` to the *physical* path is a no-op: that is exactly what `"."` already gives you. Diagnose by comparing the **raw** `directory:` against `pwd -P` — do **not** `readlink -f` it first: the URI keeps the unresolved path, so resolving before comparing reports OK exactly when the dataset is broken |
| Serve exits: `[Errno 98] error while attempting to bind on address ('127.0.0.1', 8017): address already in use` | Another data root's server already holds the port — this layout is one catalog per data root | Pick a free `uvicorn.port` in `.tilewright/config.yml` and pass the matching `--url http://localhost:<PORT>` to register. Do **not** merge two data roots into one catalog to dodge it |
| `httpx.ConnectError` / connection refused during register | Server not running, or a different port | Step 2 first; confirm Gate 1 passes |
| Register prints `failed=<N>` with a loud WARNING about child count | A crashed earlier run left a half-registered entity | Delete the dataset container (see Gate 2) and re-register; `skipped` is fine only after a clean run, `failed` never is |
| `catalog.db` appears in the code repo, not beside the data | A command ran from the repo root instead of the data root | Delete the stray DB, `cd` to the data root, re-run. This is the binding rule biting |
| Server starts but `c["<KEY>"]` is a `KeyError` | The server answering `--url` is not the one you started — a leftover on the same port, or a server launched from a different cwd (register reaches the catalog only over HTTP; its own cwd cannot pick a catalog) | Confirm via `.tilewright/server.log` that your server owns the port (Gate 1), then re-register |

## STOP

Done = Gate 1 ✅ + Gate 2 ✅ + Gate 3 ✅. Report: the `entities_added/skipped/
failed` summary line, and the shape+dtype of the array you read back through the
server.

Do not go hunting for *modelling* problems here — a contract or count problem
belongs to **tilewright-onboard**; come back when Gate B is green again. The one
edit that is yours to make is a `directory:` that is wrong *as a path* (a
logical path through a symlink, per triage): fix it, regenerate the manifest,
confirm Gate A and Gate B still pass, and continue. Onboard's gates cannot catch
that one — they both pass on the logical path, because nothing opens the URI
until a read.
