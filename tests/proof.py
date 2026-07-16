"""Acceptance proof for tilewright v2 — raw tiled API against 127.0.0.1:8017.

Run: uv run python tests/proof.py
Prints labeled blocks L1-L6 (L7 is tests/verify_live.py, run separately).
"""

import io
from time import perf_counter

import h5py
from tiled.client import from_uri
from tiled.queries import Key

from tilewright.client import locate

client = from_uri("http://127.0.0.1:8017", api_key="tcbmin")

print("=== L1 root listing ===")
print(list(client))

print("=== L2 dataset-level query: Key('data_type') == 'simulation' ===")
hits = client.search(Key("data_type") == "simulation")
print(sorted(hits))

print("=== L3 entity-level range query with timing ===")
t0 = perf_counter()
hits = client["BROAD_SIGMA"].search(Key("sigma") >= 0.04).search(Key("sigma") < 0.05)
n = len(hits)
dt = perf_counter() - t0
print(f"count={n} in {dt:.3f} s (len() is a server-side SQL COUNT over 10,000 entities)")
k = next(iter(hits))
print(f"sample hit: key={k} sigma={hits[k].metadata['sigma']}")

print("=== L4 artifact-level key lookup ===")
ls_ent = list(client["LCLS_RIXS_STATIC"])[0]
arr = client["LCLS_RIXS_STATIC"][ls_ent]["pixel"]
print(f"path: /LCLS_RIXS_STATIC/{ls_ent}/pixel")
print(f"shape={arr.shape} dtype={arr.dtype}")
cn_key = list(client["CNCS_incident_beam"])[0]
cn = client["CNCS_incident_beam"][cn_key]
print(f"CNCS pointer entity: key={cn_key}")
print(f"  globus_url={cn.metadata['globus_url']}")
print(f"  Ei={cn.metadata['Ei']}")
print(f"  len(list(entity))==0: {len(list(cn)) == 0}")
bs_key = next(iter(client["BROAD_SIGMA"]))
bs_ent = client["BROAD_SIGMA"][bs_key]
print(f"tilewright.client.locate(BROAD_SIGMA[{bs_key}]):")
print(f"  {locate(bs_ent)}")

print("=== L5 slicing read (server slices before serializing) ===")
a = bs_ent["rixs_spectrum"]
s = a[0:5, :]
print(f"a[0:5, :] -> shape={s.shape} dtype={s.dtype}")

print("=== L6 bulk export blob ===")
buf = io.BytesIO()
client["LCLS_RIXS_STATIC"][ls_ent].export(buf, format="application/x-hdf5")
b = buf.getvalue()
print(f"{len(b)} bytes, HDF5")
print('API call: client["LCLS_RIXS_STATIC"][ent].export(buf, format="application/x-hdf5")')
with h5py.File(io.BytesIO(b), "r") as f:
    print(f"datasets in blob ({len(list(f))}): {sorted(f)}")
    print(f"root attrs: {dict(f.attrs)}")
