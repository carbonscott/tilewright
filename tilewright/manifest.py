"""tilewright.manifest — v2 dataset YAML contract + Parquet manifest generation.
source is a TAGGED UNION: files | batch | table | groups (see the tilewright-onboard skill).
uid is a PROVENANCE hash: [:16] sha256 of
rel_path | "rel_path:row" | str(row[id]) | "rel_path:group_path".
Validation collects ALL errors, prints domain language, exits 1. Shape/dtype
are captured at generate time — registration never opens HDF5."""

import argparse
import hashlib
import json
import sys
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

import h5py
import numpy as np
import pandas as pd
import yaml

TOP_LEVEL_KEYS = {"key", "metadata", "source", "artifacts"}
SOURCE_TAGS = ("files", "batch", "table", "groups")
ARTIFACT_COLUMNS = ["uid", "type", "file", "dataset", "index", "shape", "dtype"]


# --- validation: explicit checks, every error collected, domain language ---

def _need_str(errors, obj, key, where):
    if not (isinstance(obj.get(key), str) and obj.get(key)):
        errors.append(f"{where} requires '{key}' (non-empty string)")


def _only_keys(errors, obj, allowed, where):
    for k in sorted(set(obj) - set(allowed)):
        errors.append(f"{where}: unknown key '{k}' (allowed: {', '.join(sorted(allowed))})")


def _check_artifacts(errors, raw, tag):
    arts = raw.get("artifacts")
    if not (isinstance(arts, list) and len(arts) >= 1):
        errors.append(f"top level requires 'artifacts' (list, min 1) when source is '{tag}'"
                      " — a sibling of 'source', e.g. artifacts: [{type: spectrum, dataset: /spectra}]")
        return
    for i, a in enumerate(arts):
        if not (isinstance(a, dict) and set(a) == {"type", "dataset"}
                and isinstance(a["type"], str) and isinstance(a["dataset"], str)):
            errors.append(f"artifacts[{i}] must be {{type: <str>, dataset: <str>}}")
    types = [a.get("type") for a in arts if isinstance(a, dict)]
    for t in sorted({t for t in types if isinstance(t, str) and types.count(t) > 1}):
        errors.append(f"artifacts: duplicate type {t!r} — types become Tiled child keys, must be unique")


def _check_entity_params(errors, body, where):
    """Per-entity params rules, shared by files and groups: a group's params sit
    exactly where a file's do, one level down — same {group, from} mapping, and the
    same explicit 'params: null' escape hatch for a source with no scalar params."""
    params = body.get("params", "absent")
    if isinstance(params, dict):
        _only_keys(errors, params, {"group", "from"}, f"{where}.params")
        _need_str(errors, params, "group", f"{where}.params")
        if params.get("from") not in ("attrs", "datasets"):
            errors.append(f"{where}.params requires 'from': attrs | datasets")
    elif params is not None:  # explicit 'params: null' == no per-entity params
        errors.append(f"{where} requires 'params' ({{group, from}} mapping,"
                      " or null: dataset declares no per-entity params)")


def validate(raw):
    """Return a list of error strings (empty list == valid contract)."""
    errors = []
    if not isinstance(raw, dict):
        return ["top level must be a mapping"]
    _only_keys(errors, raw, TOP_LEVEL_KEYS, "top level")
    _need_str(errors, raw, "key", "top level")
    md = raw.get("metadata")
    if not isinstance(md, dict):
        errors.append("top level requires 'metadata' (mapping)")
    elif not isinstance(md.get("data_type"), str):
        errors.append("metadata requires 'data_type' (string)")
    src = raw.get("source")
    if not isinstance(src, dict):
        errors.append(f"top level requires 'source' (mapping with one of: {' | '.join(SOURCE_TAGS)})")
        return errors
    _only_keys(errors, src, SOURCE_TAGS, "source")
    tags = [t for t in SOURCE_TAGS if t in src]
    if len(tags) != 1:
        errors.append(f"source requires exactly one of: {' | '.join(SOURCE_TAGS)}")
        return errors
    tag, body = tags[0], src[tags[0]]
    if not isinstance(body, dict):
        errors.append(f"source.{tag} must be a mapping")
        return errors
    _need_str(errors, body, "directory", f"source.{tag}")
    if isinstance(body.get("directory"), str) and body["directory"][:1] not in ("/", ""):
        errors.append(f"source.{tag}.directory must be an absolute path (Mode-B reads break later)")
    sbd = body.get("server_base_dir")
    if sbd is not None and not (isinstance(sbd, str) and sbd.startswith("/") and ".." not in sbd.split("/")):
        errors.append(f"source.{tag}.server_base_dir must be an absolute path without '..' — the server's own view of 'directory'")
    if tag == "files":
        _only_keys(errors, body, {"directory", "pattern", "params", "server_base_dir"}, "source.files")
        _need_str(errors, body, "pattern", "source.files")
        _check_entity_params(errors, body, "source.files")
        _check_artifacts(errors, raw, tag)
    elif tag == "groups":
        _only_keys(errors, body, {"directory", "file", "pattern", "params", "server_base_dir"},
                   "source.groups")
        _need_str(errors, body, "file", "source.groups")
        if isinstance(body.get("file"), str) and (body["file"].startswith("/") or ".." in body["file"].split("/")):
            errors.append("source.groups.file must be relative to 'directory' and free of '..' — it is joined onto the server's view of the root, which an absolute or escaping path silently discards")
        _need_str(errors, body, "pattern", "source.groups")
        if isinstance(body.get("pattern"), str) and body["pattern"].startswith("/"):
            errors.append("source.groups.pattern matches TOP-LEVEL group names inside the file"
                          " (e.g. 'sample_*'), not absolute HDF5 paths")
        _check_entity_params(errors, body, "source.groups")
        _check_artifacts(errors, raw, tag)
        for i, a in enumerate(raw.get("artifacts") or []):
            if isinstance(a, dict) and isinstance(a.get("dataset"), str) and a["dataset"].startswith("/"):
                errors.append(f"artifacts[{i}].dataset {a['dataset']!r} is resolved WITHIN each entity group"
                              " — write it relative to the group (e.g. 'data', not '/sample_1/data')")
    elif tag == "batch":
        _only_keys(errors, body, {"directory", "pattern", "params", "extra", "server_base_dir"}, "source.batch")
        _need_str(errors, body, "pattern", "source.batch")
        params = body.get("params")
        if not isinstance(params, dict):
            errors.append("source.batch requires 'params' (mapping: {group})")
        else:
            _only_keys(errors, params, {"group"}, "source.batch.params")
            _need_str(errors, params, "group", "source.batch.params")
        extra = body.get("extra", [])
        if not (isinstance(extra, list) and all(isinstance(e, str) for e in extra)):
            errors.append("source.batch 'extra' must be a list of HDF5 dataset paths")
        _check_artifacts(errors, raw, tag)
    else:  # table
        _only_keys(errors, body, {"directory", "path", "id", "locator"}, "source.table")
        _need_str(errors, body, "path", "source.table")
        _need_str(errors, body, "id", "source.table")
        locator = body.get("locator", {})
        if not (isinstance(locator, dict) and all(
                isinstance(k, str) and isinstance(v, str) for k, v in locator.items())):
            errors.append("source.table 'locator' must map column names to '{col}' string templates")
        if raw.get("artifacts") not in (None, []):
            errors.append("source.table forbids 'artifacts' (rows have no readable bytes): omit it or use []")
    return errors


def load_config(yaml_path):
    """Load + validate a dataset YAML; print every error and exit(1) on any."""
    with open(yaml_path) as fh:
        raw = yaml.safe_load(fh)
    errors = validate(raw)
    if errors:
        for e in errors:
            print(f"contract error: {e}", file=sys.stderr)
        sys.exit(1)
    return raw


def source_tag(cfg):
    """The single source tag of a validated config: files | batch | table | groups."""
    return next(t for t in SOURCE_TAGS if t in cfg["source"])


def server_dir(cfg):
    """Base joined with artifact 'file' for data_uri: the SERVER's view of the data root
    (differs from 'directory' when the server mounts the same bytes elsewhere; local reads
    keep using 'directory')."""
    body = cfg["source"][source_tag(cfg)]
    return body.get("server_base_dir") or body["directory"]


# --- generation ---

def _uid(provenance):
    """uid = provenance hash: WHERE the entity came from, not its params."""
    return hashlib.sha256(provenance.encode()).hexdigest()[:16]


def _to_python(value):
    """HDF5/numpy/pandas scalar -> plain JSON-serializable Python value."""
    if isinstance(value, np.ndarray):
        value = value.item() if value.size == 1 else value.tolist()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def _discover(directory, pattern):
    files = sorted(Path(directory).glob(pattern))
    if not files:
        raise FileNotFoundError(f"no files match {pattern!r} under {directory}")
    return files


def _h5open(fp):
    try:
        return h5py.File(fp, "r", locking=False)
    except OSError as exc:
        raise OSError(f"{fp}: cannot open as HDF5 ({exc}) — tighten the source "
                      "pattern to exclude non-HDF5 siblings") from None


def _h5get(f, fp, path, yaml_key):
    """f[path] with filename + YAML-key context instead of a bare KeyError."""
    if path not in f:
        raise KeyError(f"{fp}: {yaml_key} {path!r} not found in file")
    return f[path]


def _read_params(f, fp, group, from_, where):
    """Scalar params out of one HDF5 location: its attrs, or the 0-d datasets under it.
    Shared by files and groups — the only difference is WHICH location is passed
    (a file's root vs. one entity-group's own subgroup)."""
    loc = _h5get(f, fp, group, where)
    if from_ == "attrs":
        return {k: _to_python(loc.attrs[k]) for k in sorted(loc.attrs)}
    return {k: _to_python(loc[k][()]) for k in sorted(loc.keys())  # 0-d datasets only
            if isinstance(loc[k], h5py.Dataset) and loc[k].shape == ()}


def _generate_files(cfg):
    src = cfg["source"]["files"]
    root = Path(src["directory"])
    spec = src["params"]  # None == explicit 'params: null' (no per-entity params)
    ent_rows, art_rows = [], []
    for fp in _discover(root, src["pattern"]):
        rel = fp.relative_to(root).as_posix()
        uid = _uid(rel)
        with _h5open(fp) as f:
            params = {}
            if spec is not None:
                group, from_ = spec["group"], spec["from"]
                params = _read_params(f, fp, group, from_, "source.files.params.group")
                if not params:
                    raise ValueError(f"{fp}: no params at group={group!r} from={from_}")
                if "uid" in params:
                    raise ValueError(f"{fp}: param name 'uid' is reserved (it is the provenance hash)")
            ent_rows.append({"uid": uid, **params})
            for art in cfg["artifacts"]:
                ds = _h5get(f, fp, art["dataset"], f"artifact type={art['type']} dataset")
                art_rows.append({"uid": uid, "type": art["type"], "file": rel,
                                 "dataset": art["dataset"], "index": None,
                                 "shape": json.dumps(list(ds.shape)),
                                 "dtype": str(ds.dtype)})
    return ent_rows, art_rows


def _generate_groups(cfg):
    """One entity per matching TOP-LEVEL group inside a single file: the layout where
    an author wrote N self-contained entities into one HDF5 rather than N files
    (issue #5). Mirrors _generate_files, but keyed by internal group path instead of
    file path — so artifact 'dataset' paths are resolved WITHIN each entity's group."""
    src = cfg["source"]["groups"]
    root = Path(src["directory"])
    # Canonical rel: './many.h5' and 'many.h5' name one file, so they must hash to
    # ONE uid — what relative_to().as_posix() already gives 'files' for free.
    rel, spec = PurePosixPath(src["file"]).as_posix(), src["params"]
    fp = root / rel
    if not fp.exists():
        raise FileNotFoundError(f"source.groups file not found: {fp}")
    ent_rows, art_rows = [], []
    with _h5open(fp) as f:
        names = [n for n in sorted(f) if fnmatch(n, src["pattern"])
                 and isinstance(f[n], h5py.Group)]
        if not names:
            raise ValueError(f"{fp}: no top-level groups match pattern "
                             f"{src['pattern']!r} — it globs group NAMES, not paths")
        for name in names:
            gpath = f"/{name}"
            uid = _uid(f"{rel}:{gpath}")  # provenance: the file PLUS the internal path
            params = {}
            if spec is not None:
                pgroup = f"{gpath}/{spec['group'].strip('/')}"
                params = _read_params(f, fp, pgroup, spec["from"], "source.groups.params.group")
                if not params:
                    raise ValueError(f"{fp}: no params at group={pgroup!r} from={spec['from']}")
                if "uid" in params:
                    raise ValueError(f"{fp}: param name 'uid' is reserved (it is the provenance hash)")
            ent_rows.append({"uid": uid, **params})
            for art in cfg["artifacts"]:
                dpath = f"{gpath}/{art['dataset'].strip('/')}"
                ds = _h5get(f, fp, dpath, f"artifact type={art['type']} dataset (within {gpath})")
                art_rows.append({"uid": uid, "type": art["type"], "file": rel,
                                 "dataset": dpath, "index": None,
                                 "shape": json.dumps(list(ds.shape)),
                                 "dtype": str(ds.dtype)})
    return ent_rows, art_rows


def _generate_batch(cfg):
    src = cfg["source"]["batch"]
    root = Path(src["directory"])
    group = src["params"]["group"]
    ent_rows, art_rows = [], []
    for fp in _discover(root, src["pattern"]):
        rel = fp.relative_to(root).as_posix()
        with _h5open(fp) as f:
            n = _h5get(f, fp, cfg["artifacts"][0]["dataset"], "artifacts[0] dataset").shape[0]
            cols = {}
            grp = _h5get(f, fp, group, "source.batch.params.group")
            for name in sorted(grp.keys()):
                obj = grp[name]
                if isinstance(obj, h5py.Dataset):
                    if obj.shape[:1] != (n,):
                        raise ValueError(f"{fp}: param {name} shape {obj.shape}, "
                                         f"expected leading axis {n}")
                    cols[name] = obj[:]
            if not cols:
                raise ValueError(f"{fp}: no (N,) datasets under {group}")
            if "uid" in cols:
                raise ValueError(f"{fp}: param name 'uid' is reserved (it is the provenance hash)")
            for path in src.get("extra", []):
                arr = _h5get(f, fp, path, "source.batch.extra")[:]
                if arr.shape[0] != n:
                    raise ValueError(f"{fp}: extra {path} leading axis {arr.shape[0]} != {n}")
                cols[path.rstrip("/").split("/")[-1]] = arr
            art_info = []
            for art in cfg["artifacts"]:
                ds = _h5get(f, fp, art["dataset"], f"artifact type={art['type']} dataset")
                if ds.shape[0] != n:
                    raise ValueError(f"{fp}: artifact {art['dataset']} leading axis {ds.shape[0]} != {n}")
                art_info.append((art, json.dumps(list(ds.shape[1:])), str(ds.dtype)))
            for i in range(n):
                uid = _uid(f"{rel}:{i}")
                ent_rows.append({"uid": uid,
                                 **{k: _to_python(v[i]) for k, v in cols.items()}})
                for art, shape_json, dtype in art_info:
                    art_rows.append({"uid": uid, "type": art["type"], "file": rel,
                                     "dataset": art["dataset"], "index": i,
                                     "shape": shape_json, "dtype": dtype})
    return ent_rows, art_rows


def _generate_table(cfg):
    src = cfg["source"]["table"]
    path = Path(src["directory"]) / src["path"]
    if not path.exists():
        raise FileNotFoundError(f"sidecar table not found: {path}")
    df = pd.read_parquet(path)
    if src["id"] not in df.columns:
        raise KeyError(f"source.table id column {src['id']!r} not in {list(df.columns)}")
    if "uid" in df.columns:
        raise ValueError(f"{path}: sidecar column 'uid' is reserved (it is the provenance hash)")
    ent_rows = []
    for _, r in df.iterrows():
        params = {k: _to_python(v) for k, v in r.items()}
        row = {"uid": _uid(str(r[src["id"]])), **params}
        for col, template in src.get("locator", {}).items():
            try:
                row[col] = template.format(**params)
            except KeyError as exc:
                raise KeyError(f"locator template {col!r} references {exc} — "
                               "not a table column") from None
        ent_rows.append(row)
    return ent_rows, []


_WALKERS = {"files": _generate_files, "batch": _generate_batch, "table": _generate_table,
            "groups": _generate_groups}


def generate_manifests(cfg, outdir):
    """Walk the data per source tag; write entities.parquet + artifacts.parquet."""
    ent_rows, art_rows = _WALKERS[source_tag(cfg)](cfg)
    uids = [r["uid"] for r in ent_rows]
    if len(set(uids)) != len(uids):
        raise ValueError(f"uid collision: {len(uids) - len(set(uids))} duplicate "
                         "provenance ids (table source: the id column must be unique)")
    ent_df = pd.DataFrame(ent_rows)
    if art_rows:
        art_df = pd.DataFrame(art_rows, columns=ARTIFACT_COLUMNS)
        art_df["index"] = art_df["index"].astype("Int64")
    else:
        art_df = pd.DataFrame({c: pd.Series(dtype="Int64" if c == "index" else "string")
                               for c in ARTIFACT_COLUMNS})
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    ent_df.to_parquet(out / "entities.parquet", index=False)
    art_df.to_parquet(out / "artifacts.parquet", index=False)
    return ent_df, art_df


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="tilewright manifest",
        description="Validate a dataset YAML and generate Parquet manifests.")
    p.add_argument("yaml_path", help="dataset YAML (see the tilewright-onboard skill)")
    p.add_argument("-o", "--outdir", help="output dir for entities/artifacts.parquet")
    p.add_argument("--check", action="store_true",
                   help="validate the YAML contract only; do not touch data")
    args = p.parse_args(argv)

    cfg = load_config(args.yaml_path)
    if args.check:
        print(f"contract OK: key={cfg['key']} source={source_tag(cfg)} "
              f"artifacts={len(cfg.get('artifacts') or [])}")
        return 0
    if not args.outdir:
        p.error("-o/--outdir is required unless --check is given")
    ent_df, art_df = generate_manifests(cfg, args.outdir)
    print(f"dataset={cfg['key']} entities={len(ent_df)} artifacts={len(art_df)} "
          f"-> {args.outdir}/entities.parquet, {args.outdir}/artifacts.parquet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
