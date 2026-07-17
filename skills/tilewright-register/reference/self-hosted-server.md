# Running your own Tiled server, for testing

**You do not need this file to use the tilewright-register skill.** It exists for one
case: exercising the gates end-to-end when you have no endpoint, or when the endpoint
you were given cannot serve your paths. A server you run yourself, on the host where
the data lives, is the one place where authoring view and serving view are guaranteed
to agree.

Everything here assumes the data root is yours and `.tilewright/` sits inside it, so
the allowlist is the data root itself and never needs a per-dataset edit.

## Config

Write `.tilewright/config.yml`, substituting `<PORT>` — one port per data root, since
each root gets its own catalog and its own server:

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

`init_if_not_exists` creates `catalog.db` on first serve; there is no separate init
step. Run every command from the data root — both `uri` and `readable_storage` resolve
against the working directory.

```bash
cd <data root>
ls -d .tilewright          # must print .tilewright
nohup uv run --project <tilewright repo root> tiled serve config .tilewright/config.yml \
    --api-key tcbmin > .tilewright/server.log 2>&1 &
```

Do not bother saving `$!` — it is the `uv` wrapper, not uvicorn, so it proves nothing
and killing it orphans the real process.

## The impostor check — the server answering is *the one you started*

A curl that gets a reply proves *a* server is up, not *yours*. If a stale server from
another data root already owns the port, yours dies on `address already in use` while
the curl answers from the impostor — and registration writes your dataset into **its**
catalog.

Four tempting signals all lie: a curl answering; `Application startup complete`
(uvicorn prints it *before* the bind, so it appears in a collided log too);
`catalog.db` existing (`init_if_not_exists` creates it before the bind); and
`server.log` itself, which on a second dataset is a *previous* run's file still saying
`Uvicorn running on` long after that server died. Read live state instead:

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

The `pid=`-less case is not hypothetical on a shared login node: `ss` shows you the
*socket* of every user but the *pid* of only your own, so a colleague's server on your
port looks like an empty port unless you check `LISTEN` separately.

This check exists **only** because you started the process yourself. It reads `/proc`
and your own PID namespace, so none of it applies to an endpoint running somewhere
else — which is why the skill's default path cannot use it, and why Gate 1 asks a
question you *can* answer over HTTP instead.

## Then run the gates

Register against `--url http://localhost:<PORT> --api-key tcbmin` and run Gates 2 and 3
exactly as `SKILL.md` describes. `Refusing to serve ...` in `.tilewright/server.log` is
readable here, because the server is yours — that message is the one thing this setup
gives you that a real endpoint never will.
