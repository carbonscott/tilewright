# Using the catalog — reading a registered tilewright dataset

For **consumers** of a tilewright catalog: you have a dataset that is already
registered and serving, and the endpoint's `<URL>` plus an `<API_KEY>` on it. Getting
a dataset to that point is the **tilewright-onboard** and **tilewright-register**
skills' job (`skills/`); this file starts where they stop.

Two ways in. **Raw tiled** works from anywhere the endpoint is reachable and streams
bytes over HTTP. **Mode A** skips the server entirely and reads the source HDF5 with
h5py — only for readers sitting on the same filesystem as the data.

## Raw tiled cheat sheet

tiled's client IS the client; tilewright adds nothing on the HTTP path.

```python
from tiled.client import from_uri
from tiled.queries import Key

c = from_uri("<URL>", api_key="<API_KEY>")   # the endpoint you registered into
list(c)                                   # dataset keys
dict(c["BROAD_SIGMA"].metadata)           # dataset provenance metadata
ds = c["BROAD_SIGMA"]
len(ds)                                   # entity count

# SQL-served metadata queries (Key comparisons only; never Regex):
hits = ds.search(Key("sigma") >= 0.04).search(Key("sigma") <= 0.05)
hits = hits.search(Key("gamma") == 0.1)   # chain freely
ent = hits.values().first()
dict(ent.metadata)                        # physics params + Mode-A locators

# Sliced reads — the server reads only the requested bytes:
arr = ent["rixs_spectrum"]
arr.shape                                 # (151, 40)
arr[0:5, :]                               # numpy array, lazy adapter

# Bulk export — whole entity as one HDF5 blob, single round trip:
import io
buf = io.BytesIO()
ent.export(buf, format="application/x-hdf5")
open("entity.h5", "wb").write(buf.getvalue())
```

## Mode A — direct h5py access (same-filesystem readers)

Every registered entity carries `path_<type>` / `dataset_<type>` /
`index_<type>` locator metadata. `tilewright.client` parses it and does the
one non-trivial read (batched row index before user slice):

```python
from tiled.client import from_uri
from tiled.queries import Key
from tilewright import client as tw

c = from_uri("<URL>", api_key="<API_KEY>")   # the endpoint you registered into
ent = c["BROAD_SIGMA"].search(Key("sigma") >= 0.04).values().first()
tw.locate(ent)      # {"rixs_spectrum": {"file": ..., "dataset": ..., "index": ...}}
base = "/sdf/data/lcls/ds/prj/<project>/results/data-source/RIXS_SIM_BROAD_SIGMA"
spec = tw.load(ent, "rixs_spectrum", base)          # (151, 40), pure h5py
row0 = tw.load(ent, "rixs_spectrum", base, slc=(0, slice(None)))

# Table (pointer) entities have no artifact locators; locate() returns the
# entity metadata verbatim (sidecar columns + rendered locator columns):
cn = c["CNCS_incident_beam"].values().first()
tw.locate(cn)["globus_url"]
```

`base` is the reader's own view of the dataset root — you supply it, it is not read
from the catalog. The locators carry each file's path *relative* to that root, so a
reader whose mount differs from the registering host's passes its own `base` and Mode A
still resolves.

`load()` exists to own one detail: for `batch` sources the locator carries an `index`,
and that row must be taken along axis 0 **before** your slice. Applying them in the
other order is the classic copy-paste bug — let `load()` do it.
