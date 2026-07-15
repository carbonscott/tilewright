"""Manual live verification — requires the tcb-min server on :8017 with the
proof corpus registered. Not collected by pytest; run directly:

    uv run python tests/verify_live.py

Checks: (1) the three catalog entity counts; (2) one served HTTP slice is
numpy.array_equal to a direct h5py read of the same file+dataset+index —
direct h5py is the reference implementation, the Tiled path is the port.
"""

import sys
from pathlib import Path

import h5py
import numpy
import yaml
from tiled.client import from_uri

REPO = Path(__file__).resolve().parent.parent
URL, API_KEY = "http://localhost:8017", "tcbmin"
EXPECTED = {"LCLS_RIXS_STATIC": 1, "BROAD_SIGMA": 10000, "CNCS_incident_beam": 100}


def main():
    c = from_uri(URL, api_key=API_KEY)
    for key, n in EXPECTED.items():
        count = len(c[key])
        assert count == n, f"{key}: {count} entities != expected {n}"
        print(f"PASS {key} entity count == {n}")

    cfg = yaml.safe_load((REPO / "examples/datasets/broad_sigma.yml").read_text())
    base = cfg["source"]["batch"]["directory"]
    ent = c["BROAD_SIGMA"].values().first()
    served = ent["rixs_spectrum"][0:5, :]
    md = dict(ent.metadata)
    with h5py.File(f"{base}/{md['path_rixs_spectrum']}", "r", locking=False) as f:
        direct = f[md["dataset_rixs_spectrum"]][int(md["index_rixs_spectrum"])][0:5, :]
    assert numpy.array_equal(served, direct), "served slice != direct h5py read"
    print("PASS served HTTP slice == direct h5py (same file+dataset+index)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
