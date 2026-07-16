"""tilewright.client — Mode A only: find an entity's source files and read them
directly with h5py, bypassing the Tiled byte path.

For everything else, tiled's own client IS the client — do not wrap it:

    from tiled.client import from_uri
    from tiled.queries import Key

    c = from_uri("http://localhost:8017", api_key="tcbmin")
    list(c)                                   # dataset keys
    dict(c["BROAD_SIGMA"].metadata)           # dataset provenance metadata
    ds = c["BROAD_SIGMA"]
    hits = ds.search(Key("sigma") >= 0.04).search(Key("sigma") <= 0.05)
    len(hits)                                 # SQL-served count
    ent = hits.values().first()
    dict(ent.metadata)                        # physics params + Mode-A locators
    arr = ent["rixs_spectrum"]
    arr[0:5, :]                               # server reads only these bytes
    import io; buf = io.BytesIO()
    ent.export(buf, format="application/x-hdf5")  # whole entity, one round trip

Only ``Key`` comparisons (==, <=, >=, ...) are SQL-served in tiled 0.2.9 —
never use ``Regex`` (not SQL-backed). Mode A (this module) is for readers on
the same filesystem as the data: query with tiled, then bulk-read artifacts
with h5py at full Lustre speed.
"""

import os

import h5py


def locate(entity):
    """Parse the Mode-A locators register.py stamps on every entity.

    Returns ``{artifact_type: {"file": rel_path, "dataset": h5_path,
    "index": row_or_None}}`` parsed from the ``path_*``/``dataset_*``/
    ``index_*`` metadata keys. Table (pointer) entities carry no artifact
    locators; for them the entity metadata — sidecar columns plus rendered
    locator columns such as ``globus_url`` — is returned verbatim.
    """
    md = dict(entity.metadata)
    out = {}
    for k, v in md.items():
        if k.startswith("path_"):
            out.setdefault(k[len("path_"):], {})["file"] = v
        elif k.startswith("dataset_"):
            out.setdefault(k[len("dataset_"):], {})["dataset"] = v
        elif k.startswith("index_"):
            out.setdefault(k[len("index_"):], {})["index"] = v
    for loc in out.values():
        loc.setdefault("index", None)
    return out if out else md


def load(entity, artifact_type, base_dir, slc=None):
    """Read one artifact directly with h5py (Mode A), honoring batched index.

    ``base_dir`` is the dataset YAML's source directory (the locators' file
    paths are relative to it). ``index`` is set only for batch-source
    artifacts: the artifact is row ``index`` along axis 0 of the HDF5
    dataset, so it must be applied BEFORE the user slice — getting that
    order wrong is the classic copy-paste bug this function exists to own.
    """
    loc = locate(entity)[artifact_type]
    with h5py.File(os.path.join(base_dir, loc["file"]), "r", locking=False) as f:
        ds = f[loc["dataset"]]
        if loc["index"] is not None:
            row = ds[int(loc["index"])]
            return row[slc] if slc is not None else row
        return ds[slc] if slc is not None else ds[...]
