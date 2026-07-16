---
name: tilewright-register
description: Register an already-manifested tilewright dataset into a Tiled catalog and prove it serves — lay out .tilewright/config.yml so the data root is its own allowlist, start the server, register the Parquet manifests over HTTP, then read one array back through the server to prove the bytes flow. Use when a dataset already has a validated dataset YAML and manifest (the tilewright-onboard skill's Gate B has passed) and it now needs to be in the catalog and queryable. Do NOT use to fix a dataset YAML's modelling (contract, params, entity/artifact counts) or to onboard a dataset whose structure is not yet described — that is tilewright-onboard. One exception — a source directory that is wrong as a path (logical or symlinked) is this skill's to fix and regenerate, because onboard's gates both pass on it.
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
code. The one exception is a symlinked root you cannot regenerate (see triage).

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

**For `files` and `batch`, the raw `directory:` must equal `pwd -P` or sit
literally beneath it.** Skip this check entirely for `table`: its `directory:`
only locates the sidecar Parquet, it registers no assets, and it need not be
under the root — or server-readable — at all.

If it does not match, one of two things is true:

- it points at an unrelated tree → `.tilewright/` is beside the wrong data;
  move it;
- `readlink -f <directory>` *does* land under `pwd -P` → it is a logical path
  through a symlink. It will be refused anyway, because the test compares the
  paths as written and never resolves them. Rewrite `directory:` as the
  physical path and regenerate the manifest.

Do not skip this: registration succeeds either way, and only the first read
fails.

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

If there is no config yet, copy this and substitute `<PORT>` — one port per
data root, since each root gets its own catalog and its own server. Everything
else is verbatim:

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

**If you take that route, Gate 1's cwd check no longer applies** — it identifies
your server *by* the cwd both values resolve against, and you have just removed
that coupling, so it will call your own correct server an impostor. Everything
below assumes the relative config this step writes. With an absolutized config,
verify ownership by config path instead:

```bash
tr '\0' ' ' < /proc/$TILED_PID/cmdline    # must name THIS root's .tilewright/config.yml
```

## Step 2 — serve (background it; it must outlive this step)

The server must outlive this command, so start it detached. A foreground
`tiled serve` blocks until killed — there is no second terminal here.

```bash
nohup uv run --project <tilewright repo root> tiled serve config .tilewright/config.yml \
    --api-key tcbmin > .tilewright/server.log 2>&1 &
```

Creates `.tilewright/catalog.db` on first run.

Do not bother saving `$!` — it is the `uv` wrapper, not uvicorn, so it proves
nothing about the server and killing it orphans the real process. Gate 1 finds
the true PID below; use that one to stop the server when you are done.

### Gate 1 — the server answering is *the one you started*

A curl that gets a reply proves *a* server is up, not *your* server. If a stale
server from another data root already owns the port, yours dies on `address
already in use` while the curl answers from the impostor — and registration
writes your dataset into **its** catalog. What happens next depends on the
impostor's allowlist: usually Gate 3 then fails with a 500 you will misdiagnose
(its root does not admit your data), but if its allowlist happens to cover your
files, all three gates go green with your dataset sitting in a catalog you will
never look in. Either way it landed somewhere you did not intend.

Three tempting signals all lie. Do not build the gate on any of them:

- **A curl answering** — an impostor on the port answers exactly like your
  server.
- **`Application startup complete`** — uvicorn prints it *before* attempting
  the bind, so it appears in a healthy log and a collided one alike (measured:
  1 and 1).
- **`.tilewright/catalog.db` existing** — `init_if_not_exists` creates it
  before the bind, so it appears even for a server that never started.

And the log itself is not enough: on the second dataset you did not start the
server this session, so `server.log` is a *previous* run's file that keeps
saying `Uvicorn running on` long after that server died.

Ask the only question that matters — **is the process listening on this port
serving *this* data root?** — by reading live state:

```bash
PORT=<PORT>
if ! command -v ss >/dev/null; then
  echo "Gate 1 INCONCLUSIVE — ss (iproute2) is missing; do not proceed"
else
  for i in $(seq 60); do
    ss -lntH "sport = :$PORT" | grep -q LISTEN && break
    sleep 1
  done
  TILED_PID=$(ss -lptnH "sport = :$PORT" | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)

  if [ -n "$TILED_PID" ] && [ "$(readlink -f /proc/$TILED_PID/cwd)" = "$(pwd -P)" ]; then
    echo "Gate 1 PASS — the server on $PORT (pid $TILED_PID) serves THIS root"
  elif [ -n "$TILED_PID" ]; then
    echo "Gate 1 FAIL — IMPOSTOR on $PORT: it serves $(readlink -f /proc/$TILED_PID/cwd), not $(pwd -P)"
  elif ss -lntH "sport = :$PORT" | grep -q LISTEN; then
    echo "Gate 1 FAIL — $PORT is held by ANOTHER USER's process (no pid visible); pick a free uvicorn.port"
  else
    echo "Gate 1 FAIL — nothing listening on $PORT; read .tilewright/server.log"
  fi
fi
```

The `pid=`-less case is not hypothetical on a shared login node: `ss` shows you
the *socket* of every user but the *pid* of only your own, so a colleague's
server on your port looks like an empty port unless you check `LISTEN`
separately. Do not drop the `2>/dev/null`-free form either — a missing `ss`
must be loud, not silently read as "nothing listening".

**Gate 1 passes only on `Gate 1 PASS`.** This works identically whether you just
started the server or are adding a second dataset to a root whose server has
been up for days — it carries no state between commands and trusts no log. Since
both `uri` and `readable_storage` resolve against the server's cwd, a server
whose cwd is this root *is* this root's server; anything else on the port is
somebody else's catalog, and registering into it writes your dataset somewhere
you will not find it.

On PASS, `$TILED_PID` is the real uvicorn process — use it (not `$!`) to stop
the server later. If it reports `nothing listening`, read
`.tilewright/server.log`: `address already in use` means another root holds the
port — change `uvicorn.port`. A restart is needed only if you edit `config.yml` — which, per the layout
above, adding a dataset never requires.

**Register against the same `<PORT>` this gate just checked.** Gate 1 vouches
for a port, not for the `--url` you type next; point them at different servers
and the gate certifies one catalog while your dataset goes into another.

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

Check 3 is not redundant with checks 1 and 2: it is what catches entities that
were *created* but whose artifacts never attached — the 415 case below, where
the entity count is perfect and the arrays are missing.

On a *deliberate re-run* of an already-complete dataset, expect
`entities_added=0 artifacts_added=0 skipped=<N> failed=0` — check 3 does not
apply, and nothing in this summary can distinguish "already complete" from
"silently broken". Only Gate 3 can. Re-run means re-read.

**Read the summary line, never the exit code.** `tilewright register` exits 0
even when every row failed: errors are caught, printed to stderr as `FAILED
...`, tallied into `failed=`, and then the command returns 0 anyway. `$?`,
`&&`, and `set -e` will all report success on a total failure.

**`skipped` does not mean "complete", and re-registering never *updates*
anything.** An entity is counted `skipped` when its child *count* matches the
manifest — the children are never inspected, and nothing about them is
rewritten. Two consequences, both of which show `failed=0`:

- A previous run that failed mid-artifact leaves committed-but-empty children,
  so a re-run reports `entities_added=0 skipped=N failed=0` — Gate 2 green — on
  a catalog that 500s on every read.
- **Changing a path in the YAML and regenerating the manifest does not change
  the registered asset.** Fix a wrong `directory:`, regenerate, re-register:
  the catalog still holds the *old* URI and Gate 3 still fails, while Gate 2
  reports a clean `skipped=N failed=0`.

**Delete the dataset container before re-registering whenever a previous run
failed *or* you changed any path in the YAML.** Do not wait for `failed>0` — the
path case never produces it:

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
is instead: the entity metadata round-trips (`c["<KEY>"][ent].metadata`) with
its sidecar columns — plus rendered locator columns if, and only if, the YAML
declares a `locator:` block (it is optional). Say so in your report rather than skipping the
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
| Same `Refusing to serve` for data that IS under the root | Symlinked root: `readable_storage: ["."]` becomes the **physical** cwd, `directory:` is a **logical** path, and the containment test never resolves either | **Preferred:** rewrite `directory:` physically (`readlink -f`), regenerate the manifest, **delete the container and re-register** — a plain re-run keeps the old URI and reports `skipped failed=0` while Gate 3 still 500s. **Only if you cannot regenerate:** *add* the logical path alongside `"."` (`- "."` then `- "<logical path>"`) and restart. Never **replace** `"."` with the logical path: this config is the whole root's, and a lone logical allowlist refuses every dataset here whose `directory:` is physical — which onboard's `pwd -P` rule makes the norm. Measured: replacing → the sibling dataset 500s; adding → both serve. Setting `readable_storage` to the *physical* path is a no-op **when the server's cwd is the data root** — that is exactly what `"."` already gives you there. Diagnose by comparing the **raw** `directory:` against `pwd -P`; do **not** `readlink -f` it first, or you get OK exactly when the dataset is broken |
| Serve exits: `[Errno 98] error while attempting to bind on address ('127.0.0.1', 8017): address already in use` | Another data root's server already holds the port — this layout is one catalog per data root | Pick a free `uvicorn.port` in `.tilewright/config.yml` and pass the matching `--url http://localhost:<PORT>` to register. Do **not** merge two data roots into one catalog to dodge it |
| `httpx.ConnectError` / connection refused during register | Server not running, or a different port | Step 2 first; confirm Gate 1 passes |
| Register prints `failed=<N>` with a loud WARNING about child count | A crashed earlier run left a half-registered entity | Delete the dataset container (see Gate 2) and re-register; `skipped` is fine only after a clean run, `failed` never is |
| `catalog.db` appears in the code repo, not beside the data | A command ran from the repo root instead of the data root | Delete the stray DB, `cd` to the data root, re-run. This is the binding rule biting |
| Server starts but `c["<KEY>"]` is a `KeyError` | The server answering `--url` is not the one you started — a leftover on the same port, or a server launched from a different cwd (register reaches the catalog only over HTTP; its own cwd cannot pick a catalog) | Re-run **Gate 1** — the `/proc/<pid>/cwd` check, not the log. `server.log` is a dead run's file that still says `Uvicorn running on` long after that server exited, so it will "confirm" ownership of a port an impostor now holds. Re-register only once Gate 1 passes |

## STOP

Done = Gate 1 ✅ + Gate 2 ✅ + Gate 3 ✅. Report: the `entities_added/skipped/
failed` summary line, and the shape+dtype of the array you read back through the
server.

Do not go hunting for *modelling* problems here — a contract or count problem
belongs to **tilewright-onboard**; come back when Gate B is green again. The one
edit that is yours to make is a `directory:` that is wrong *as a path* (a
logical path through a symlink, per triage). Onboard's gates cannot catch that
one — they both pass on the logical path, because nothing opens the URI until a
read. Fix it like this, and do not omit the delete:

1. rewrite `directory:` physically and regenerate the manifest;
2. confirm Gate A and Gate B still pass;
3. **delete the dataset container** — the registered asset still carries the old
   URI, and re-registering will not replace it;
4. re-register and re-run Gate 3.

Skip the delete (item 3 above) and you get `skipped=N failed=0` with Gate 3
still returning 500 — the same triage row you just came from.
