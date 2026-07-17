---
name: tilewright-register
description: Register an already-manifested tilewright dataset into a Tiled catalog that is already running and prove it serves. You are handed the catalog's URL and API key; you do not start a server. First prove the endpoint resolves the same absolute paths your manifests carry, then register the Parquet manifests over HTTP, then read one array back through the endpoint to prove the bytes flow. Use when a dataset already has a validated dataset YAML and manifest (the tilewright-onboard skill's Gate B has passed) and it now needs to be in the catalog and queryable. Do NOT use to fix a dataset YAML's modelling (contract, params, entity/artifact counts) or to onboard a dataset whose structure is not yet described — that is tilewright-onboard. Two edits are this skill's to make — server_base_dir when the endpoint resolves your paths somewhere else, and a source directory that is wrong as a path (logical or symlinked), because onboard's gates both pass on it.
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
export UV_CACHE_DIR="$SCRATCH/.uv-cache"   # S3DF: home is ~24 GB; keep uv's cache on $SCRATCH. Omit off S3DF.
uv run --project <tilewright repo root> tilewright ...
```

**No endpoint to register into?** You do not need one to exercise these gates.
**`${CLAUDE_SKILL_DIR}/reference/self-hosted-server.md`** covers running your own
server on the host where the data lives — including the impostor check that proves
the server answering on your port is the one you started, which no log line can.
You need it only for that case; everything below assumes the endpoint you were given.

## Gate 1 — does this endpoint resolve *your* paths?

**Run this before you register anything.** It is the only gate that can save
you, because the two gates after it cannot see this failure.

`tilewright register` builds each asset URI directly from the **raw,
unresolved** `directory:` string in your YAML:

```
file://localhost{directory}/{file}
```

The endpoint must see that identical absolute path, or it can never open your
data. The authoring host and the serving host are not the same machine, and
they do not have to agree about what a file is called — when they disagree,
`server_base_dir` is what reconciles them, and the next section shows how.

Ask the endpoint what path its existing assets carry. Only leaf array nodes hold
assets, so this walks down to the first one it finds — a `data_uri` is not
visible at the catalog root, and a query that stops there returns nothing and
tells you nothing:

```bash
uv run --project <tilewright repo root> python -c "
from tiled.client import from_uri
c = from_uri('<URL>', api_key='<API_KEY>')
def find(node, path=(), depth=0):
    if depth > 3:
        return None
    for k in node:
        child = node[k]
        ds = getattr(child, 'data_sources', None)
        ds = ds() if callable(ds) else ds
        if ds:
            return path + (k,), ds[0].assets
        if hasattr(child, 'items'):          # containers only; never recurse into an array
            hit = find(child, path + (k,), depth + 1)
            if hit:
                return hit
    return None
hit = find(c)
if hit:
    print('/'.join(hit[0]))
    for a in hit[1]:
        print(a.data_uri)
else:
    print('INCONCLUSIVE — no asset found; Gate 1 did not run')
"
```

It searches every branch, not just the first. A catalog whose first dataset is a
`table` holds no assets there, and a probe that only tried the first key would
report nothing while a sibling carried the answer.

```
BROAD_SIGMA/BROAD_SIGMA_6dc97c22e692e/rixs_spectrum
file://localhost/<project>/data-source/RIXS_SIM_BROAD_SIGMA/batch_0/simulations.h5
```

Compare that prefix against the raw string your manifests will use — **do not
`readlink -f` it first**, or you destroy the signal you are looking for:

```bash
grep -m1 'directory:' .tilewright/datasets/<KEY>.yml
```

**Gate 1 passes only if the endpoint's prefix and your `directory:` describe the
same absolute path**, or you set `server_base_dir` to bridge them. If they
differ, read the next section before you register — not after.

Three things this gate cannot tell you, so do not over-read it:

- **Printing nothing is not a pass.** An empty catalog, a catalog holding only
  zero-artifact (`table`) datasets, and a walk that ran out of depth all print
  `INCONCLUSIVE` — never "no mismatch". If it prints that, you have not run the
  gate; Gate 3 becomes your only probe.
- **One leaf is one dataset.** This prints the first asset it finds. A catalog
  can hold datasets registered from different hosts under different prefixes,
  so a prefix that matches yours proves that *dataset's* author agreed with the
  server — not that you do.
- `table` datasets register **no assets at all** — their `directory:` only
  locates a sidecar Parquet, which is read at registration and never served.
  Gate 1 does not apply to them, and a path mismatch cannot hurt them.

### When the prefixes differ — set `server_base_dir`

Differing is normal, not broken. A deployed pod mounts the same bytes somewhere
else, and nothing about your data is wrong.

**Measured on the MAIQMag deployment (2026-07), for one and the same file:**

| | |
|---|---|
| what the endpoint serves | `file://localhost/<project>/LS/static/S_52.h5` |
| what the authoring host calls it | `/sdf/data/lcls/ds/prj/<project>/results/LS/static/S_52.h5` |
| does `/<project>` exist on the authoring host? | no |

188 of 188 sampled assets on that endpoint carry the mapped prefix; none carry
`/sdf`. The pod reads its own path fine — the two hosts simply disagree about
what that file is called.

Tell the dataset what the server calls its directory. Beside `directory:`, in
the same `files:` or `batch:` block:

```yaml
source:
  files:
    directory: /sdf/data/lcls/ds/prj/<project>/results/LS/static   # what YOU open
    server_base_dir: /<project>/LS/static                          # what the SERVER opens
    pattern: "*.h5"
```

`server_base_dir` replaces `directory:` **only** when building the asset URI.
Everything that reads your files locally — manifest generation, onboard's Gate
B — keeps using `directory:`. That split is the whole point: one string cannot
be both, which is why writing the server's path into `directory:` is not a
workaround. It would break Gate B before you ever got here, because that path
does not exist on your host.

Derive it, do not eyeball it. Gate 1 prints one full URI, and nothing in that
string marks where the mount ends and the dataset's own tail begins — so
subtract, do not guess:

1. take the printed path, e.g. `/<project>/data-source/RIXS_SIM_BROAD_SIGMA/batch_0/simulations.h5`;
2. strip that leaf's **manifest `file` value** off the end — not just the
   filename. For `batch` the `file` is a relative path like
   `batch_0/simulations.h5`, so stripping only `simulations.h5` leaves you one
   level too deep and registers `failed=0` while serving nothing. What remains
   is the server's view of *that dataset's* `directory:`;
3. diff it against that dataset's own `directory:` to get the root mapping
   (here `/sdf/data/lcls/ds/prj/<project>/results` → `/<project>`);
4. apply that mapping to *your* `directory:`.

If the leaf came from a different author's dataset you cannot do step 3 — ask
the operator for the mapping rather than inventing one. Absent the key the URI
is byte-identical to before, so leave it out when the prefixes already agree,
and `table` datasets never need it at all.

**An absolute-but-wrong value is validated and still fails.** The check rejects
a relative path and `..`; it cannot know what the pod mounts. A typo here
registers clean and dies at Gate 3 as the same bare 500. Gate 3 is what proves
you got it right.

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

Prove the bytes flow:

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

1. Re-run Gate 1. Differing prefixes are a `server_base_dir` problem, not an
   allowlist problem.
2. Confirm `directory:` is physical, not a logical path through a symlink (the
   containment test compares paths as written and never resolves them). Rewrite
   it physically, regenerate, delete the container, re-register.
3. Still 500? **Ask the endpoint's operator** whether your root is under their
   `readable_storage` and whether their server loads the broker adapter. Give
   them the exact `data_uri` from your manifest **and the `x-tiled-request-id`
   correlation ID off the 500** — the client echoes it in the exception, and it
   sits beside the `Refusing to serve` line in their log. It is the one thing
   that turns "it 500s" into a line number for someone who can read that log.

**Do not edit that server's config, and do not ask for the allowlist to be
widened to admit one dataset.** If a whole data root is not servable, that is a
deployment conversation, not a registration step.

## Error triage — symptom, cause, fix

Against an endpoint you do not own, the client sees very little. Match on what
you *can* see.

| Symptom | Cause | Fix |
|---|---|---|
| Gate 1 shows the endpoint's prefix differs from your `directory:` | Authoring host and serving host disagree about the absolute path | Set `server_base_dir` beside `directory:` to the server's view, delete the container if you already registered, re-register. `table` datasets never need it |
| Gate 2 prints `FAILED artifact ...: 415: The given data source mimetype, application/x-hdf5-broker, is not one that the Tiled server knows how to read` | The endpoint has no adapter bound for the broker mimetype — this fails at **registration**, not at read | Not fixable from your side on a foreign endpoint; ask the operator, then delete the container and re-register (Gate 2) |
| Gate 3 returns a bare **500**, `{"detail":"Internal server error"}` | Ambiguous by construction — path view, allowlist, adapter, or an unreadable file. The explanatory line is in the server's log, which you cannot read | Work the ordered list in the allowlist section above. Do not guess |
| `httpx.ConnectError` / connection refused | Wrong `<URL>`, or the endpoint is unreachable from this host | Some endpoints resolve only from inside the facility network. Check reachability before blaming the catalog |
| `401` naming scopes it wanted | Your key lacks a scope | Read the message — it names the required and the held scopes (see Gate 2 for the deletion case) |
| Register prints `failed=<N>` with a loud WARNING about child count | A crashed earlier run left a half-registered entity | Delete the dataset container (see Gate 2) and re-register; `skipped` is fine only after a clean run, `failed` never is |
| `c["<KEY>"]` is a `KeyError` right after a green Gate 2 | You registered into a different catalog than you are reading — the `--url` and the read URL disagree | Use the same `<URL>` and `<API_KEY>` for Gate 2 and Gate 3. Nothing else links them; `tilewright register` reaches the catalog only over HTTP |

## STOP

Done = Gate 1 ✅ (or *inconclusive* / not-applicable, said out loud) + Gate 2 ✅
+ Gate 3 ✅. Report: the `entities_added/skipped/failed` summary line, and the
shape+dtype of the array you read back through the endpoint. If Gate 1 failed,
report *that*, and do not report a green Gate 2 as though it meant anything.

Do not go hunting for *modelling* problems here — a contract or count problem
belongs to **tilewright-onboard**; come back when Gate B is green again. Two
edits are yours: `server_base_dir`, when Gate 1 shows the endpoint sees your
data somewhere else; and a `directory:` that is wrong *as a path* (a logical
path through a symlink), per step 2 above. Onboard's gates cannot catch that
second one — they both pass on the logical path, because nothing opens the URI
until a read.
