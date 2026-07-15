"""tcb_min.manifest — v2 dataset YAML contract + Parquet manifest generation.

The contract is a TAGGED UNION: `source` holds exactly one of
  files: one matched HDF5 file = one entity (params from one group,
         as attributes or 0-d datasets);
  batch: entities are rows along axis 0 inside each matched file
         ((N,) datasets under params.group are the param columns);
  table: passthrough — sidecar Parquet rows ARE the entities, zero artifacts
and each tag owns only its own keys — illegal combos are unrepresentable.

uid is a PROVENANCE hash, never a param hash: files -> sha256(rel_path),
batch -> sha256("rel_path:row"), table -> sha256(str(row[id])); [:16].
Identical params in two files = two entities (correct); params are pure
queryable metadata. Validation is explicit: ALL errors collected, printed
in domain language, exit(1); no type coercion. Shape and dtype are captured
HERE, at generate time — registration never opens HDF5.

Run:  python -m tcb_min.manifest datasets/foo.yml -o manifests/FOO [--check]
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
import yaml

TOP_LEVEL_KEYS = {"key", "metadata", "source", "artifacts"}
SOURCE_TAGS = ("files", "batch", "table")
ARTIFACT_COLUMNS = ["uid", "type", "file", "dataset", "index", "shape", "dtype"]


# ---------------------------------------------------------------------------
# Validation — explicit checks, every error collected, domain language
# ---------------------------------------------------------------------------

def _need_str(errors, obj, key, where):
    if not (isinstance(obj.get(key), str) and obj.get(key)):
        errors.append(f"{where} requires '{key}' (non-empty string)")


def _only_keys(errors, obj, allowed, where):
    for k in sorted(set(obj) - set(allowed)):
        errors.append(f"{where}: unknown key '{k}' (allowed: {', '.join(sorted(allowed))})")


def _check_artifacts(errors, raw, tag):
    arts = raw.get("artifacts")
    if not (isinstance(arts, list) and len(arts) >= 1):
        errors.append(f"source.{tag} requires 'artifacts' (list, min 1)")
        return
    for i, a in enumerate(arts):
        if not (isinstance(a, dict) and set(a) == {"type", "dataset"}
                and isinstance(a["type"], str) and isinstance(a["dataset"], str)):
            errors.append(f"artifacts[{i}] must be {{type: <str>, dataset: <str>}}")


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
        errors.append("top level requires 'source' (mapping with one of: files | batch | table)")
        return errors
    _only_keys(errors, src, SOURCE_TAGS, "source")
    tags = [t for t in SOURCE_TAGS if t in src]
    if len(tags) != 1:
        errors.append("source requires exactly one of: files | batch | table")
        return errors
    tag, body = tags[0], src[tags[0]]
    if not isinstance(body, dict):
        errors.append(f"source.{tag} must be a mapping")
        return errors
    _need_str(errors, body, "directory", f"source.{tag}")
    if tag == "files":
        _only_keys(errors, body, {"directory", "pattern", "params"}, "source.files")
        _need_str(errors, body, "pattern", "source.files")
        params = body.get("params")
        if not isinstance(params, dict):
            errors.append("source.files requires 'params' (mapping: {group, from})")
        else:
            _only_keys(errors, params, {"group", "from"}, "source.files.params")
            _need_str(errors, params, "group", "source.files.params")
            if params.get("from") not in ("attrs", "datasets"):
                errors.append("source.files.params requires 'from': attrs | datasets")
        _check_artifacts(errors, raw, tag)
    elif tag == "batch":
        _only_keys(errors, body, {"directory", "pattern", "params", "extra"}, "source.batch")
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
    """The single source tag of a validated config: files | batch | table."""
    return next(t for t in SOURCE_TAGS if t in cfg["source"])


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

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


def _generate_files(cfg):
    src = cfg["source"]["files"]
    root = Path(src["directory"])
    group, from_ = src["params"]["group"], src["params"]["from"]
    ent_rows, art_rows = [], []
    for fp in _discover(root, src["pattern"]):
        rel = fp.relative_to(root).as_posix()
        uid = _uid(rel)
        with h5py.File(fp, "r", locking=False) as f:
            loc = f[group]
            if from_ == "attrs":
                params = {k: _to_python(loc.attrs[k]) for k in sorted(loc.attrs)}
            else:  # datasets: 0-dimensional datasets directly under the group
                params = {k: _to_python(loc[k][()]) for k in sorted(loc.keys())
                          if isinstance(loc[k], h5py.Dataset) and loc[k].shape == ()}
            if not params:
                raise ValueError(f"{fp}: no params at group={group!r} from={from_}")
            ent_rows.append({"uid": uid, **params})
            for art in cfg["artifacts"]:
                if art["dataset"] not in f:
                    raise KeyError(f"{fp}: artifact dataset {art['dataset']!r} not found")
                ds = f[art["dataset"]]
                art_rows.append({"uid": uid, "type": art["type"], "file": rel,
                                 "dataset": art["dataset"], "index": None,
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
        with h5py.File(fp, "r", locking=False) as f:
            n = f[cfg["artifacts"][0]["dataset"]].shape[0]
            cols = {}
            for name in sorted(f[group].keys()):
                obj = f[group][name]
                if isinstance(obj, h5py.Dataset):
                    if obj.shape[:1] != (n,):
                        raise ValueError(f"{fp}: param {name} shape {obj.shape}, "
                                         f"expected leading axis {n}")
                    cols[name] = obj[:]
            if not cols:
                raise ValueError(f"{fp}: no (N,) datasets under {group}")
            for path in src.get("extra", []):
                arr = f[path][:]
                if arr.shape[0] != n:
                    raise ValueError(f"{fp}: extra {path} leading axis != {n}")
                cols[path.rstrip("/").split("/")[-1]] = arr
            art_info = []
            for art in cfg["artifacts"]:
                ds = f[art["dataset"]]
                if ds.shape[0] != n:
                    raise ValueError(f"{fp}: artifact {art['dataset']} leading axis != {n}")
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


_WALKERS = {"files": _generate_files, "batch": _generate_batch, "table": _generate_table}


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
        prog="python -m tcb_min.manifest",
        description="Validate a dataset YAML and generate Parquet manifests.",
    )
    p.add_argument("yaml_path", help="dataset YAML (see ONBOARDING.md)")
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
