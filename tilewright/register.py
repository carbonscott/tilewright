"""tilewright.register — manifests + YAML -> HTTP registration into Tiled.

Registers Dataset -> Entity -> Artifact over HTTP against a running Tiled
server; HDF5 files are referenced in place (Management.external), shape and
dtype come from the manifest — this module never imports h5py. Idempotent,
fail-loud: an existing complete entity counts as skipped; a half-registered
one (array children != manifest count) prints a loud WARNING and counts as
failed. Entities register in parallel (ThreadPoolExecutor, 8 workers).
"""

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
from tiled.client import from_uri
from tiled.structures.array import ArrayStructure, BuiltinDtype
from tiled.structures.core import StructureFamily
from tiled.structures.data_source import Asset, DataSource, Management

from tilewright.manifest import load_config, source_tag

BROKER_MIMETYPE = "application/x-hdf5-broker"


def to_json_safe(value):
    """Manifest cell -> JSON-serializable metadata value."""
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, (list, dict)):
        return value
    if value is None or (not isinstance(value, (str, bool)) and pd.isna(value)):
        return None
    return value


def _register_artifact(ent_container, directory, art_row):
    shape = tuple(json.loads(art_row["shape"]))
    dtype = np.dtype(art_row["dtype"])
    structure = ArrayStructure(
        data_type=BuiltinDtype.from_numpy_dtype(dtype),
        shape=shape,
        chunks=tuple((n,) for n in shape),  # single chunk per dim
    )
    parameters = {"dataset": art_row["dataset"]}
    index = art_row.get("index")
    if pd.notna(index):
        parameters["slice"] = str(int(index))
    full_path = os.path.join(directory, art_row["file"])
    data_source = DataSource(
        structure_family=StructureFamily.array,
        structure=structure,
        mimetype=BROKER_MIMETYPE,
        parameters=parameters,
        management=Management.external,
        assets=[Asset(
            data_uri=f"file://localhost{full_path}",
            is_directory=False,
            parameter="data_uris",
        )],
    )
    ent_container.new(
        StructureFamily.array,
        [data_source],
        key=art_row["type"],
        metadata={"type": art_row["type"], "shape": list(shape),
                  "dtype": art_row["dtype"]},
    )


def _register_one_entity(parent, dataset_key, directory, ent_row, art_group):
    """Returns (entities_added, artifacts_added, skipped, failed)."""
    uid = str(ent_row["uid"])
    ent_key = f"{dataset_key}_{uid[:13]}"
    if ent_key in parent:
        existing = len(parent[ent_key])
        if existing == len(art_group):
            return (0, 0, 1, 0)
        print(f"WARNING half-registered entity {ent_key}: has {existing} array "
              f"children, manifest expects {len(art_group)} — counted as failed; "
              "delete it and re-register", file=sys.stderr)
        return (0, 0, 0, 1)
    metadata = {col: to_json_safe(val) for col, val in ent_row.items()}
    for _, art in art_group.iterrows():
        metadata[f"path_{art['type']}"] = art["file"]
        metadata[f"dataset_{art['type']}"] = art["dataset"]
        if pd.notna(art.get("index")):
            metadata[f"index_{art['type']}"] = int(art["index"])
    ent_container = parent.create_container(key=ent_key, metadata=metadata)
    art_added = art_failed = 0
    for _, art in art_group.iterrows():
        try:
            _register_artifact(ent_container, directory, art)
            art_added += 1
        except Exception as exc:
            print(f"FAILED artifact {ent_key}/{art['type']}: {exc}", file=sys.stderr)
            art_failed += 1
    return (1, art_added, 0, art_failed)


def register_dataset(cfg, ent_df, art_df, url, api_key, max_workers=8):
    client = from_uri(url, api_key=api_key)
    directory = cfg["source"][source_tag(cfg)]["directory"]
    if cfg["key"] in client:
        parent = client[cfg["key"]]
    else:
        parent = client.create_container(key=cfg["key"], metadata=dict(cfg["metadata"]))
    grouped = dict(tuple(art_df.groupby("uid"))) if len(art_df) else {}
    empty = art_df.iloc[0:0]
    totals = [0, 0, 0, 0]  # entities_added, artifacts_added, skipped, failed
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(_register_one_entity, parent, cfg["key"],
                        directory, row, grouped.get(row["uid"], empty))
            for _, row in ent_df.iterrows()
        ]
        for fut in as_completed(futures):
            try:
                delta = fut.result()
            except Exception as exc:
                print(f"FAILED entity: {exc}", file=sys.stderr)
                delta = (0, 0, 0, 1)
            totals = [a + b for a, b in zip(totals, delta)]
    print(f"dataset={cfg['key']} entities_added={totals[0]} "
          f"artifacts_added={totals[1]} skipped={totals[2]} failed={totals[3]}")
    return tuple(totals)


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="tilewright register",
        description="Register manifests into a running Tiled server over HTTP.",
    )
    p.add_argument("yaml_path", help="dataset YAML (see the tcb-onboard skill)")
    p.add_argument("--manifests", required=True,
                   help="dir containing entities.parquet + artifacts.parquet")
    p.add_argument("--url", default="http://localhost:8017")
    p.add_argument("--api-key", default="tcbmin")
    p.add_argument("--max-workers", type=int, default=8)
    args = p.parse_args(argv)

    cfg = load_config(args.yaml_path)
    mdir = Path(args.manifests)
    ent_df = pd.read_parquet(mdir / "entities.parquet")
    art_df = pd.read_parquet(mdir / "artifacts.parquet")
    register_dataset(cfg, ent_df, art_df, args.url, args.api_key,
                     max_workers=args.max_workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
