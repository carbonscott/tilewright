"""tcb_min.client — thin physicist surface over a tcb-min Tiled catalog.

    from tcb_min import client as tcb
    c = tcb.connect("http://localhost:8017", api_key="tcbmin")
    tcb.datasets(c)                                   # {key: metadata}
    hits = tcb.find(c, "BROAD_SIGMA", sigma=(0.04, 0.05))   # lazy search
    ent = hits.values().first()
    tcb.locate(ent)                                   # Mode-A / Globus locators
    tcb.fetch(ent, "rixs_spectrum", slc=(0, slice(None)))   # sliced numpy read
    tcb.export_entity(ent)                            # whole entity as HDF5 bytes
"""

import io

from tiled.client import from_uri
from tiled.queries import Key

_LOCATOR_PREFIXES = ("path_", "dataset_", "index_", "globus_")


def connect(url, api_key=None):
    """Connect to the Tiled server; returns the root container client."""
    return from_uri(url, api_key=api_key)


def datasets(client):
    """Top-level dataset containers: {key: metadata dict}."""
    return {key: dict(client[key].metadata) for key in client}


def find(client, dataset_key, **ranges):
    """Server-side (SQL) metadata search inside one dataset.

    Each keyword is a queryable entity-metadata key. A 2-tuple/list value
    ``sigma=(0.04, 0.05)`` becomes ``0.04 <= sigma <= 0.05`` (inclusive);
    any other value becomes an equality test. Returns the lazy search
    result — iterate keys, or ``.values()`` for entity clients.
    """
    result = client[dataset_key]
    for name, value in ranges.items():
        if isinstance(value, (tuple, list)) and len(value) == 2:
            lo, hi = value
            result = result.search(Key(name) >= lo).search(Key(name) <= hi)
        else:
            result = result.search(Key(name) == value)
    return result


def locate(entity):
    """Mode-A locators from entity metadata: path_*/dataset_*/index_*/globus_*.

    Use these to open the source files directly (h5py, Globus) without
    pulling bytes through Tiled.
    """
    return {
        k: v for k, v in entity.metadata.items()
        if k.startswith(_LOCATOR_PREFIXES)
    }


def fetch(entity, artifact_key, slc=None):
    """Read one artifact (optionally sliced) as a numpy array.

    The server reads only the requested bytes (lazy adapter), so slicing a
    huge array is cheap.
    """
    arr = entity[artifact_key]
    return arr.read(slc) if slc is not None else arr.read()


def export_entity(entity):
    """Whole entity (all artifacts + metadata as attrs) as HDF5 file bytes.

    Single round trip; no server byte cap. Write the result to disk and open
    with h5py.
    """
    buf = io.BytesIO()
    entity.export(buf, format="application/x-hdf5")
    return buf.getvalue()
