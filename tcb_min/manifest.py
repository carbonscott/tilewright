"""tcb_min.manifest — dataset YAML contract + Parquet manifest generation.

The YAML contract is validated with explicit pydantic v2 models — zero
heuristics, every field authored by a human. Three layouts:

- per_entity: one HDF5 file per entity. Params from root scalars, root
  attributes, or scalar datasets under a named group.
- batched:    entities are rows along axis 0 of stacked datasets in one or
  more HDF5 files. Params must come from a group of (N,) datasets.
- pointer:    no locally readable artifact bytes. Params come from a sidecar
  Parquet table (one row per entity); optional locator templates render
  per-row pointer columns (e.g. Globus URLs). Artifacts must be [].

Outputs: <outdir>/entities.parquet + <outdir>/artifacts.parquet.
Shape and dtype are captured HERE, at generate time — registration never
opens HDF5.

Run:  python -m tcb_min.manifest datasets/foo.yml -o manifests/FOO [--check]
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import h5py
import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, model_validator

ARTIFACT_COLUMNS = [
    "uid", "type", "file", "dataset", "index",
    "shape", "dtype", "file_size", "file_mtime",
]


# --------------------------------------------------------------------------
# Contract (pydantic v2, explicit, zero heuristics)
# --------------------------------------------------------------------------

class Layout(str, Enum):
    per_entity = "per_entity"
    batched = "batched"
    pointer = "pointer"


class ParamLocation(str, Enum):
    root_scalars = "root_scalars"
    root_attributes = "root_attributes"
    group = "group"
    sidecar = "sidecar"


class DatasetMetadata(BaseModel):
    """data_type is required; everything else is open (extra allowed)."""
    model_config = ConfigDict(extra="allow")
    data_type: str


class DataSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    directory: str
    layout: Layout
    file_pattern: Optional[str] = None   # per_entity / batched only
    sidecar: Optional[str] = None        # pointer only


class ParametersSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location: ParamLocation
    group: Optional[str] = None          # required iff location == group

    @model_validator(mode="after")
    def _group_iff_group_location(self) -> "ParametersSection":
        if self.location == ParamLocation.group and not self.group:
            raise ValueError("parameters.group is required when location == 'group'")
        if self.location != ParamLocation.group and self.group:
            raise ValueError("parameters.group is only allowed when location == 'group'")
        return self


class ArtifactSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    dataset: str


class SharedSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    dataset: str


class ExtraMetadataSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset: str


class DatasetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    label: Optional[str] = None
    metadata: DatasetMetadata
    data: DataSection
    parameters: ParametersSection
    artifacts: List[ArtifactSpec]
    shared: List[SharedSpec] = []
    extra_metadata: List[ExtraMetadataSpec] = []
    locator: Dict[str, str] = {}         # pointer only: {column: template}
    provenance: Dict[str, Any] = {}

    @model_validator(mode="after")
    def _cross_field_rules(self) -> "DatasetConfig":
        layout = self.data.layout
        location = self.parameters.location
        if layout == Layout.pointer:
            if location != ParamLocation.sidecar:
                raise ValueError("pointer layout requires parameters.location == 'sidecar'")
            if not self.data.sidecar:
                raise ValueError("pointer layout requires data.sidecar (a Parquet file)")
            if self.data.file_pattern:
                raise ValueError("data.file_pattern is not allowed for pointer layout")
            if self.artifacts:
                raise ValueError("pointer layout requires artifacts: [] (no readable bytes)")
            if self.shared or self.extra_metadata:
                raise ValueError("shared/extra_metadata are not allowed for pointer layout")
        else:
            if location == ParamLocation.sidecar:
                raise ValueError("parameters.location 'sidecar' is only valid for pointer layout")
            if self.data.sidecar:
                raise ValueError("data.sidecar is only valid for pointer layout")
            if self.locator:
                raise ValueError("locator templates are only valid for pointer layout")
            if not self.data.file_pattern:
                raise ValueError(f"data.file_pattern is required for {layout.value} layout")
            if not self.artifacts:
                raise ValueError(f"{layout.value} layout requires at least 1 artifact")
        if layout == Layout.batched and location != ParamLocation.group:
            raise ValueError("batched layout requires parameters.location == 'group'")
        return self


def load_config(yaml_path: str) -> DatasetConfig:
    with open(yaml_path) as fh:
        raw = yaml.safe_load(fh)
    return DatasetConfig.model_validate(raw)


# --------------------------------------------------------------------------
# Generation helpers
# --------------------------------------------------------------------------

def _to_python(value: Any) -> Any:
    """HDF5/numpy/pandas scalar -> plain JSON-serializable Python value."""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, np.ndarray):
        value = value.item() if value.size == 1 else value.tolist()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def make_uid(key: str, params: Dict[str, Any]) -> str:
    """Content-addressed uid: sha256 of {ns: dataset key, params: canonical}."""
    canonical = {
        k: (round(v, 12) if isinstance(v, float) else v)
        for k, v in sorted(params.items())
    }
    payload = json.dumps({"ns": key, "params": canonical}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _file_stat(path: Path) -> tuple:
    st = path.stat()
    mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
    return st.st_size, mtime


def _last_segment(dataset_path: str) -> str:
    return dataset_path.rstrip("/").split("/")[-1]


def _discover_files(cfg: DatasetConfig) -> List[Path]:
    root = Path(cfg.data.directory)
    files = sorted(root.glob(cfg.data.file_pattern))
    if not files:
        raise FileNotFoundError(
            f"No files match {cfg.data.file_pattern!r} under {root}"
        )
    return files


def _params_from_file(f: h5py.File, params: ParametersSection) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if params.location == ParamLocation.root_scalars:
        for name in sorted(f.keys()):
            obj = f[name]
            if isinstance(obj, h5py.Dataset) and obj.shape == ():
                out[name] = _to_python(obj[()])
    elif params.location == ParamLocation.root_attributes:
        for name in sorted(f.attrs):
            out[name] = _to_python(f.attrs[name])
    elif params.location == ParamLocation.group:
        grp = f[params.group]
        for name in sorted(grp.keys()):
            obj = grp[name]
            if isinstance(obj, h5py.Dataset):
                out[name] = _to_python(obj[()])
    if not out:
        raise ValueError(
            f"No parameters found in {f.filename} for location={params.location.value}"
        )
    return out


# --------------------------------------------------------------------------
# Layout walkers
# --------------------------------------------------------------------------

def _generate_per_entity(cfg: DatasetConfig) -> tuple:
    root = Path(cfg.data.directory)
    ent_rows, art_rows = [], []
    for fp in _discover_files(cfg):
        rel = fp.relative_to(root).as_posix()
        fsize, fmtime = _file_stat(fp)
        with h5py.File(fp, "r", locking=False) as f:
            params = _params_from_file(f, cfg.parameters)
            uid = make_uid(cfg.key, params)
            row = {"uid": uid, **params}
            for spec in cfg.extra_metadata:  # excluded from uid hash
                row[_last_segment(spec.dataset)] = _to_python(f[spec.dataset][()])
            ent_rows.append(row)
            for art in cfg.artifacts:
                if art.dataset not in f:
                    raise KeyError(f"{fp}: artifact dataset {art.dataset!r} not found")
                ds = f[art.dataset]
                art_rows.append({
                    "uid": uid, "type": art.type, "file": rel,
                    "dataset": art.dataset, "index": None,
                    "shape": json.dumps(list(ds.shape)), "dtype": str(ds.dtype),
                    "file_size": fsize, "file_mtime": fmtime,
                })
    return ent_rows, art_rows


def _generate_batched(cfg: DatasetConfig) -> tuple:
    root = Path(cfg.data.directory)
    ent_rows, art_rows = [], []
    for fp in _discover_files(cfg):
        rel = fp.relative_to(root).as_posix()
        fsize, fmtime = _file_stat(fp)
        with h5py.File(fp, "r", locking=False) as f:
            n = f[cfg.artifacts[0].dataset].shape[0]
            grp = f[cfg.parameters.group]
            param_cols = {}
            for name in sorted(grp.keys()):
                obj = grp[name]
                if not isinstance(obj, h5py.Dataset):
                    continue
                if obj.shape[:1] != (n,):
                    raise ValueError(
                        f"{fp}: param {name} has shape {obj.shape}, expected leading axis {n}"
                    )
                param_cols[name] = obj[:]
            if not param_cols:
                raise ValueError(f"{fp}: no (N,) datasets under {cfg.parameters.group}")
            extra_cols = {}
            for spec in cfg.extra_metadata:
                arr = f[spec.dataset][:]
                if arr.shape[0] != n:
                    raise ValueError(
                        f"{fp}: extra_metadata {spec.dataset} leading axis != {n}"
                    )
                extra_cols[_last_segment(spec.dataset)] = arr
            art_info = []
            for art in cfg.artifacts:
                ds = f[art.dataset]
                if ds.shape[0] != n:
                    raise ValueError(f"{fp}: artifact {art.dataset} leading axis != {n}")
                art_info.append(
                    (art.type, art.dataset, json.dumps(list(ds.shape[1:])), str(ds.dtype))
                )
            for i in range(n):
                params = {k: _to_python(v[i]) for k, v in param_cols.items()}
                uid = make_uid(cfg.key, params)
                row = {"uid": uid, **params}
                for name, arr in extra_cols.items():
                    row[name] = _to_python(arr[i])
                ent_rows.append(row)
                for art_type, ds_path, shape_json, dtype_str in art_info:
                    art_rows.append({
                        "uid": uid, "type": art_type, "file": rel,
                        "dataset": ds_path, "index": i,
                        "shape": shape_json, "dtype": dtype_str,
                        "file_size": fsize, "file_mtime": fmtime,
                    })
    return ent_rows, art_rows


def _generate_pointer(cfg: DatasetConfig) -> tuple:
    sidecar = Path(cfg.data.directory) / cfg.data.sidecar
    if not sidecar.exists():
        raise FileNotFoundError(f"Sidecar not found: {sidecar}")
    df = pd.read_parquet(sidecar)
    ent_rows = []
    for _, r in df.iterrows():
        params = {k: _to_python(v) for k, v in r.items()}
        uid = make_uid(cfg.key, params)
        row = {"uid": uid, **params}
        for col, template in cfg.locator.items():
            try:
                row[col] = template.format(**params)
            except KeyError as exc:
                raise KeyError(
                    f"locator template {col!r} references {exc} — not a sidecar column"
                ) from None
        ent_rows.append(row)
    return ent_rows, []


def _empty_artifacts_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "uid": pd.Series(dtype="string"),
        "type": pd.Series(dtype="string"),
        "file": pd.Series(dtype="string"),
        "dataset": pd.Series(dtype="string"),
        "index": pd.Series(dtype="Int64"),
        "shape": pd.Series(dtype="string"),
        "dtype": pd.Series(dtype="string"),
        "file_size": pd.Series(dtype="int64"),
        "file_mtime": pd.Series(dtype="string"),
    })


_WALKERS = {
    Layout.per_entity: _generate_per_entity,
    Layout.batched: _generate_batched,
    Layout.pointer: _generate_pointer,
}


def generate_manifests(cfg: DatasetConfig, outdir: str) -> tuple:
    """Walk the data per layout; write entities.parquet + artifacts.parquet."""
    ent_rows, art_rows = _WALKERS[cfg.data.layout](cfg)
    uids = [r["uid"] for r in ent_rows]
    if len(set(uids)) != len(uids):
        raise ValueError(
            f"uid collision: {len(uids) - len(set(uids))} duplicate parameter sets"
        )
    ent_df = pd.DataFrame(ent_rows)
    if art_rows:
        art_df = pd.DataFrame(art_rows, columns=ARTIFACT_COLUMNS)
        art_df["index"] = art_df["index"].astype("Int64")
    else:
        art_df = _empty_artifacts_frame()
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    ent_df.to_parquet(out / "entities.parquet", index=False)
    art_df.to_parquet(out / "artifacts.parquet", index=False)
    return ent_df, art_df


def main(argv=None) -> int:
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
        print(f"contract OK: key={cfg.key} layout={cfg.data.layout.value} "
              f"location={cfg.parameters.location.value} artifacts={len(cfg.artifacts)}")
        return 0
    if not args.outdir:
        p.error("-o/--outdir is required unless --check is given")
    ent_df, art_df = generate_manifests(cfg, args.outdir)
    print(f"dataset={cfg.key} entities={len(ent_df)} artifacts={len(art_df)} "
          f"-> {args.outdir}/entities.parquet, {args.outdir}/artifacts.parquet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
