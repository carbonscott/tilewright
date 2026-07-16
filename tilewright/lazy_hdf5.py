"""LazyHDF5ArrayAdapter — per-slice HDF5 array adapter for Tiled 0.2.9.

The stock ``tiled.adapters.hdf5.HDF5ArrayAdapter`` dask-wraps the dataset and
reads the ENTIRE array per request before applying the user slice. This
adapter honors the ``parameters={"dataset": ..., "slice": ...}`` stored on
each data source at registration time and does direct
``h5py.Dataset[base_index][user_slice]`` indexing, reading only the bytes the
client asked for.

Dispatched via ``adapters_by_mimetype`` on the private mimetype
``application/x-hdf5-broker`` (see config.yml), so it coexists with the stock
``application/x-hdf5`` adapter.
"""

import copy
from typing import Any, List, Optional, Union

import h5py
import numpy
from numpy.typing import NDArray

from tiled.adapters.core import Adapter
from tiled.catalog.orm import Node
from tiled.ndslice import NDBlock, NDSlice
from tiled.structures.array import ArrayStructure
from tiled.structures.core import Spec, StructureFamily
from tiled.structures.data_source import DataSource
from tiled.type_aliases import JSON
from tiled.utils import path_from_uri


class LazyHDF5ArrayAdapter(Adapter[ArrayStructure]):
    """Array adapter for single-slice external HDF5 artifacts.

    ``base_index`` is the row index into the leading axis of the underlying
    HDF5 dataset (batched layout), or None if the artifact is the full
    dataset. The registered structure has shape ``ds.shape[1:]`` when
    ``base_index is not None``.
    """

    structure_family = StructureFamily.array

    def __init__(
        self,
        file_path: str,
        dataset_path: str,
        base_index: Optional[int],
        structure: ArrayStructure,
        *,
        metadata: Optional[JSON] = None,
        specs: Optional[List[Spec]] = None,
    ) -> None:
        self._file_path = file_path
        self._dataset_path = dataset_path
        self._base_index = base_index
        super().__init__(structure, metadata=metadata, specs=specs)

    @classmethod
    def from_catalog(
        cls,
        data_source: DataSource[ArrayStructure],
        node: Node,
        /,
        dataset: Optional[str] = None,
        slice: Optional[Union[str, int]] = None,
        **_ignored: Any,
    ) -> "LazyHDF5ArrayAdapter":
        """Build the adapter from a catalog row.

        Tiled unpacks the data source's ``parameters`` dict into the
        ``dataset``/``slice`` kwargs when invoking this classmethod.
        """
        assets = data_source.assets
        data_uris = [
            ast.data_uri for ast in assets if ast.parameter == "data_uris"
        ] or [assets[0].data_uri]
        file_path = path_from_uri(data_uris[0])

        if dataset is None:
            raise ValueError(
                "LazyHDF5ArrayAdapter requires parameters['dataset'] "
                "(the HDF5 path of the source dataset)."
            )

        base_index: Optional[int]
        if slice is None or slice == "":
            base_index = None
        else:
            base_index = int(slice)

        # Validate on-disk shape/dtype against the registered structure so a
        # stale catalog fails loudly instead of serving garbage.
        with h5py.File(file_path, "r", locking=False) as f:
            ds = f[dataset]
            full_shape = tuple(ds.shape)
            ds_dtype = ds.dtype

        expected_shape = full_shape[1:] if base_index is not None else full_shape
        registered_shape = tuple(data_source.structure.shape)
        if expected_shape != registered_shape:
            raise ValueError(
                f"Shape mismatch for {file_path}:{dataset}[{base_index}]: "
                f"registered={registered_shape}, on_disk={expected_shape}"
            )
        registered_dtype = data_source.structure.data_type.to_numpy_dtype()
        if ds_dtype != registered_dtype:
            raise ValueError(
                f"Dtype mismatch for {file_path}:{dataset}: "
                f"registered={registered_dtype}, on_disk={ds_dtype}"
            )

        return cls(
            file_path,
            dataset,
            base_index,
            data_source.structure,
            metadata=copy.deepcopy(node.metadata_),
            specs=node.specs,
        )

    def read(self, slice: NDSlice = NDSlice(...)) -> NDArray[Any]:
        """Read the user-requested slice, opening h5py per request.

        Per-request open is ~0.5 ms on Lustre and avoids cross-thread
        file-handle sharing.
        """
        with h5py.File(self._file_path, "r", locking=False) as f:
            ds = f[self._dataset_path]
            if self._base_index is not None:
                row = ds[self._base_index]
                arr = row[tuple(slice)] if slice else row
            else:
                arr = ds[tuple(slice)] if slice else ds[...]
            return numpy.asarray(arr)

    def read_block(
        self, block: NDBlock, slice: NDSlice = NDSlice(...)
    ) -> NDArray[Any]:
        """Read a dask-style block of the registered artifact."""
        block_slice = block.slice_from_chunks(self._structure.chunks)
        with h5py.File(self._file_path, "r", locking=False) as f:
            ds = f[self._dataset_path]
            if self._base_index is not None:
                arr = ds[self._base_index][tuple(block_slice)]
            else:
                arr = ds[tuple(block_slice)]
            if slice:
                arr = arr[tuple(slice)]
            return numpy.asarray(arr)
