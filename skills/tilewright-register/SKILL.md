---
name: tilewright-register
description: Register an already-manifested tilewright dataset into a Tiled catalog that is already running and prove it serves. You are handed the catalog's URL and API key; you do not start a server. First prove the endpoint resolves the same absolute paths your manifests carry, then register the Parquet manifests over HTTP, then read one array back through the endpoint to prove the bytes flow. Use when a dataset already has a validated dataset YAML and manifest (the tilewright-onboard skill's Gate B has passed) and it now needs to be in the catalog and queryable. Do NOT use to fix a dataset YAML's modelling (contract, params, entity/artifact counts) or to onboard a dataset whose structure is not yet described — that is tilewright-onboard. One exception — a source directory that is wrong as a path (logical or symlinked) is this skill's to fix and regenerate, because onboard's gates both pass on it.
allowed-tools: Read, Write, Edit, Bash
---

# tilewright-register — put a manifested dataset into the catalog

Take a dataset that already cleared **tilewright-onboard**'s Gate A + Gate B and
make it live in a Tiled catalog **that is already running**. Registration never
opens HDF5 — so a manifest can register perfectly and still serve nothing. Only
the read-back proves it works. That is why the last gate is not optional.

**Precondition:** `.tilewright/datasets/<KEY>.yml` and
`.tilewright/manifests/<KEY>/{entities,artifacts}.parquet` both exist, where
`<KEY>` is the `key:` value inside that YAML — the file and the manifest
directory are both named after it. If they do not exist, stop and use
**tilewright-onboard** first.

**You are given, and do not create:** `<URL>`, the endpoint (e.g.
`https://host/tiled-test`), and `<API_KEY>`, a key on it carrying write scopes.
Someone else runs this server — you cannot read its log, its config, or its
filesystem. Everything you learn about it, you learn over HTTP, which is exactly
why Gate 1 exists.

**Always pass both explicitly.** `tilewright register` defaults `--url` to
`http://localhost:8017` and `--api-key` to `tcbmin` — a local-server default
that will silently point you at the wrong catalog, or at nothing.

The tilewright CLI is installed in the repo, not beside the data. Reach it
without leaving the data root — `--project` selects the environment and does
**not** change the working directory:

```bash
export UV_CACHE_DIR=/sdf/data/lcls/ds/prj/prjmaiqmag01/results/.UV_CACHE   # S3DF only — omit elsewhere
uv run --project <tilewright repo root> tilewright ...
```

## Gate 1 — does this endpoint resolve *your* paths?

**Run this before you register anything.** It is the only gate that can save
you, because the two gates after it cannot see this failure.

`tilewright register` builds each asset URI directly from the **raw,
unresolved** `directory:` string in your YAML:

```
file://localhost{directory}/{file}
```

There is **no mapping layer**. The endpoint must see that identical absolute
path, or it can never open your data. The authoring host and the serving host
are not the same machine, and they do not have to agree about what a file is
called.

Ask the endpoint what path prefix its existing assets carry:

```bash
curl -s -H "Authorization: Apikey <API_KEY>" \
  "<URL>/api/v1/search/?include_data_sources=true" \
  | grep -o '"data_uri":"[^"]*"' | head -5
```

Compare it against the raw string your manifests will use — **do not
`readlink -f` it first**, or you destroy the signal you are looking for:

```bash
grep -m1 'directory:' .tilewright/datasets/<KEY>.yml
```

**Gate 1 passes only if the endpoint's prefix and your `directory:` describe the
same absolute path.** If they differ, stop here and read the next section — do
not register.

Two things this gate cannot tell you, so do not over-read it:

- An **empty catalog** has no assets to compare against. Gate 1 is then
  inconclusive, not passed. Gate 3 becomes your only probe.
- `table` datasets register **no assets at all** — their `directory:` only
  locates a sidecar Parquet, which is read at registration and never served.
  Gate 1 does not apply to them, and a path mismatch cannot hurt them.

### When the prefixes differ — the blocker

This is not a config error you can fix, and it is not something the endpoint's
operator can fix for you. It is a known gap in `tilewright/register.py`.

**Measured on the MAIQMag deployment (2026-07), for one and the same file:**

| | |
|---|---|
| what the endpoint serves | `file://localhost/prjmaiqmag01/LS/static/S_52.h5` |
| what the authoring host calls it | `/sdf/data/lcls/ds/prj/prjmaiqmag01/results/LS/static/S_52.h5` |
| does `/prjmaiqmag01` exist on the authoring host? | no |

188 of 188 sampled assets on that endpoint carry the mapped prefix; none carry
`/sdf`. The pod reads its own path fine — the two hosts simply disagree about
what that file is called.

`tilewright register` would emit the `/sdf` form, which the endpoint cannot
open. **The missing piece is the `server_base_dir` mapping** — an optional YAML
key that says "the server calls this directory something else", joined into the
URI at registration. It was deliberately cut on the assumption that authoring
host == serving host, and that assumption does not hold for a deployed pod. See
`.ai/docs/FINDINGS.md` ("`server_base_dir` path mapping") for the re-entry path.

Until that key exists, **`files` and `batch` datasets cannot be served from an
endpoint whose path view differs from yours.** Do not try to work around it by
writing the server's path into `directory:` — that same string is what
manifest generation opens locally, so a path that only exists on the server
breaks Gate B before you ever get here. Decoupling those two readers is
precisely what `server_base_dir` is for.

What to do instead: say so in your report, and stop. `table` datasets are
unaffected and may proceed. To exercise the gates end-to-end meanwhile, run
your own server against your own paths — see the appendix.

## Gate 2 — register

```bash
uv run --project <tilewright repo root> tilewright register .tilewright/datasets/<KEY>.yml \
    --manifests .tilewright/manifests/<KEY> --url <URL> --api-key <API_KEY>
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

**Registration does not validate paths.** A URI the endpoint cannot open is
accepted exactly like one it can — measured: both a correct and a wrong prefix
registered clean, `failed=0`. Gate 2 going green says nothing about whether the
endpoint can read a single byte.

**Read the summary line, never the exit code.** `tilewright register` exits 0
even when every row failed: errors are caught, printed to stderr as `FAILED
...`, tallied into `failed=`, and then the command returns 0 anyway. `$?`,
`&&`, and `set -e` will all report success on a total failure.

**`skipped` does not mean "complete", and re-registering never *updates*
anything.** An entity is counted `skipped` when its child *count* matches the
manifest — the children are never inspected, and nothing about them is
rewritten. So a half-finished earlier run, or a path you fixed in the YAML,
both re-register to a clean `entities_added=0 skipped=N failed=0` on a catalog
that still 500s on every read.

**Delete the dataset container before re-registering whenever a previous run
failed *or* you changed any path in the YAML.** Do not wait for `failed>0` — the
path case never produces it:

```bash
uv run --project <tilewright repo root> python -c "
from tiled.client import from_uri
c = from_uri('<URL>', api_key='<API_KEY>')
c['<KEY>'].delete(recursive=True)   # NOT del c['<KEY>'] — containers reject item deletion
print('deleted <KEY>; catalog keys now:', list(c))
"
```

Deleting needs `delete:node` **and** `delete:revision` on your key. With only
the first, the refusal is a misleading **401** that names the scopes it wanted —
read it, do not assume your key is wrong.

A bare `Retrying...` on stderr from the tiled client is a harmless transient;
judge only the summary line.

## Gate 3 — read one array back (the gate that actually proves it)

Registration copies paths and shapes; it never opens the data. Gate 2 can pass
on a dataset the endpoint cannot read a single byte of. Prove the bytes flow:

```bash
uv run --project <tilewright repo root> python -c "
from tiled.client import from_uri
c = from_uri('<URL>', api_key='<API_KEY>')
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
declares a `locator:` block (it is optional). Say so in your report rather than
skipping the gate silently.

This gate reads **one** artifact of one entity. That is enough to prove the
adapter, the path view, and the allowlist all work — the failures this skill is
about are per-dataset, not per-file — but it is not a survey of every file. A
single unreadable file among thousands will not surface here.

## Whose allowlist governs, and what to do when it refuses

Tiled serves an asset only if its path is under the **server's**
`readable_storage`. On an endpoint you were handed, that list belongs to
whoever deploys it, it lives in their deployment repo, and **it is not yours to
edit.** It is usually not even readable over the API — measured: `/api/v1/config`
and `/api/v1/admin/config` both 404 for a normal key, and no route in
`openapi.json` exposes it.

So you cannot look it up. You can only observe it, and what you observe is thin.
**A refusal reaches you as a bare `500` with body `{"detail":"Internal server
error"}`.** The explanatory line — `Refusing to serve file://localhost/<path>
because it is outside the readable storage area for this server` — goes to the
*server's* log, inside a pod you have no shell on. There is no log for you to
read.

That 500 is ambiguous by construction: an allowlist refusal, a path-view
mismatch (Gate 1), a missing `application/x-hdf5-broker` adapter, and a
genuinely unreadable file all look identical from the client. That is why Gate 1
earns its place — it removes the likeliest cause *before* it becomes an
unexplained 500.

**What to do — in order:**

1. Re-run Gate 1. Differing prefixes are the blocker above, not an allowlist
   problem.
2. Confirm `directory:` is physical, not a logical path through a symlink (the
   containment test compares paths as written and never resolves them). Rewrite
   it physically, regenerate, delete the container, re-register.
3. Still 500? **Ask the endpoint's operator** whether your root is under their
   `readable_storage` and whether their server loads the broker adapter. Give
   them the exact `data_uri` from your manifest — the one fact they need and
   cannot guess.

**Do not edit that server's config, and do not ask for the allowlist to be
widened to admit one dataset.** If a whole data root is not servable, that is a
deployment conversation, not a registration step.

## Error triage — symptom, cause, fix

Against an endpoint you do not own, the client sees very little. Match on what
you *can* see.

| Symptom | Cause | Fix |
|---|---|---|
| Gate 1 shows the endpoint's prefix differs from your `directory:` | Authoring host and serving host disagree about the absolute path | The blocker above. `server_base_dir` does not exist yet; report and stop. `table` datasets are unaffected |
| Gate 2 prints `FAILED artifact ...: 415: The given data source mimetype, application/x-hdf5-broker, is not one that the Tiled server knows how to read` | The endpoint has no adapter bound for the broker mimetype — this fails at **registration**, not at read | Not fixable from your side on a foreign endpoint; ask the operator. Then **delete the dataset container and re-register** — the failed run left empty children that a plain re-run would count as `skipped`, hiding the breakage behind a green Gate 2 |
| Gate 3 returns a bare **500**, `{"detail":"Internal server error"}` | Ambiguous by construction — path view, allowlist, adapter, or an unreadable file. The explanatory line is in the server's log, which you cannot read | Work the ordered list in the allowlist section above. Do not guess |
| Gate 2 green, Gate 3 500, and you *did* fix a path since the last run | Re-registering never rewrites a registered URI; the catalog still holds the old one | Delete the container and re-register. `skipped=N failed=0` on a broken catalog is the expected symptom, not a contradiction |
| `httpx.ConnectError` / connection refused | Wrong `<URL>`, or the endpoint is unreachable from this host | Some endpoints resolve only from inside the facility network. Check reachability before blaming the catalog |
| `401` naming scopes it wanted | Your key lacks a scope — deletion needs `delete:node` **and** `delete:revision` | Read the message; it names the required and held scopes. Mint a key with the scopes you need |
| Register prints `failed=<N>` with a loud WARNING about child count | A crashed earlier run left a half-registered entity | Delete the dataset container (see Gate 2) and re-register; `skipped` is fine only after a clean run, `failed` never is |
| `c["<KEY>"]` is a `KeyError` right after a green Gate 2 | You registered into a different catalog than you are reading — the `--url` and the read URL disagree | Use the same `<URL>` and `<API_KEY>` for Gate 2 and Gate 3. Nothing else links them; `tilewright register` reaches the catalog only over HTTP |

## STOP

Done = Gate 1 ✅ + Gate 2 ✅ + Gate 3 ✅. Report: the `entities_added/skipped/
failed` summary line, and the shape+dtype of the array you read back through the
endpoint. If Gate 1 failed, report *that*, and do not report a green Gate 2 as
though it meant anything.

Do not go hunting for *modelling* problems here — a contract or count problem
belongs to **tilewright-onboard**; come back when Gate B is green again. The one
edit that is yours to make is a `directory:` that is wrong *as a path* (a logical
path through a symlink), per step 2 above. Onboard's gates cannot catch that one
— they both pass on the logical path, because nothing opens the URI until a read.

---

## Appendix (optional) — running your own server, for testing

**You do not need this section to use the skill.** It exists for one case:
exercising the gates end-to-end when you have no endpoint, or when the endpoint
you were given cannot serve your paths (the blocker above). A server you run
yourself, on the host where the data lives, is the one place where authoring
view and serving view are guaranteed to agree.

Everything here assumes the data root is yours and `.tilewright/` sits inside
it, so the allowlist is the data root itself and never needs a per-dataset edit.

Write `.tilewright/config.yml`, substituting `<PORT>` — one port per data root,
since each root gets its own catalog and its own server:

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
| `adapters_by_mimetype` | Binds the `application/x-hdf5-broker` mimetype the manifests carry to tilewright's lazy reader. **Without this block, registration itself fails with 415** — not optional, not read-time-only. |
| `readable_storage: ["."]` | `.tilewright/`'s parent — the data root itself. The allowlist is the data root, so it never changes. |
| `uvicorn.port` | **Give each data root its own port.** Two roots both defaulting to 8017 collide, and the collision is silent and dangerous — see the impostor check below. |

`init_if_not_exists` creates `catalog.db` on first serve; there is no separate
init step. Run every command from the data root — both `uri` and
`readable_storage` resolve against the working directory.

```bash
cd <data root>
ls -d .tilewright          # must print .tilewright
nohup uv run --project <tilewright repo root> tiled serve config .tilewright/config.yml \
    --api-key tcbmin > .tilewright/server.log 2>&1 &
```

Do not bother saving `$!` — it is the `uv` wrapper, not uvicorn, so it proves
nothing and killing it orphans the real process.

### The impostor check — the server answering is *the one you started*

A curl that gets a reply proves *a* server is up, not *yours*. If a stale server
from another data root already owns the port, yours dies on `address already in
use` while the curl answers from the impostor — and registration writes your
dataset into **its** catalog.

Four tempting signals all lie: a curl answering; `Application startup complete`
(uvicorn prints it *before* the bind, so it appears in a collided log too);
`catalog.db` existing (`init_if_not_exists` creates it before the bind); and
`server.log` itself, which on a second dataset is a *previous* run's file still
saying `Uvicorn running on` long after that server died. Read live state
instead:

```bash
PORT=<PORT>
if ! command -v ss >/dev/null; then
  echo "INCONCLUSIVE — ss (iproute2) is missing; do not proceed"
else
  for i in $(seq 60); do
    ss -lntH "sport = :$PORT" | grep -q LISTEN && break
    sleep 1
  done
  TILED_PID=$(ss -lptnH "sport = :$PORT" | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)

  if [ -n "$TILED_PID" ] && [ "$(readlink -f /proc/$TILED_PID/cwd)" = "$(pwd -P)" ]; then
    echo "PASS — the server on $PORT (pid $TILED_PID) serves THIS root"
  elif [ -n "$TILED_PID" ]; then
    echo "FAIL — IMPOSTOR on $PORT: it serves $(readlink -f /proc/$TILED_PID/cwd), not $(pwd -P). Change uvicorn.port and re-serve"
  elif ss -lntH "sport = :$PORT" | grep -q LISTEN; then
    echo "FAIL — $PORT is held by ANOTHER USER's process (no pid visible); pick a free uvicorn.port"
  else
    echo "FAIL — nothing listening on $PORT; your server never bound or already exited"
  fi
fi
```

The `pid=`-less case is not hypothetical on a shared login node: `ss` shows you
the *socket* of every user but the *pid* of only your own, so a colleague's
server on your port looks like an empty port unless you check `LISTEN`
separately.

This check exists **only** because you started the process yourself. It reads
`/proc` and your own PID namespace, so none of it applies to an endpoint running
somewhere else — which is why the default path above cannot use it, and why
Gate 1 asks a question you *can* answer over HTTP instead.

Then register against `--url http://localhost:<PORT> --api-key tcbmin` and run
Gates 2 and 3 exactly as above. `Refusing to serve ...` in `.tilewright/server.log`
is readable here, because the server is yours — that message is the one thing
this appendix gives you that a real endpoint never will.
