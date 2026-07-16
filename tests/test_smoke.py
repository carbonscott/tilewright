"""Offline smoke tests — no server required.

Run from the repo root (a host that can see the /sdf proof-corpus data):

    uv run --with pytest pytest tests/ -v

Three budgets are enforced here:
  1. the proof corpus generates exactly the expected entity/artifact counts;
  2. total source LOC in tilewright/ stays <= 750;
  3. the contract's top-level concept set never grows past 4 keys.
"""

from pathlib import Path

import pytest

from tilewright.manifest import (
    ARTIFACT_COLUMNS,
    TOP_LEVEL_KEYS,
    generate_manifests,
    load_config,
    server_dir,
    source_tag,
    validate,
)
from tilewright.register import _register_artifact

REPO = Path(__file__).resolve().parent.parent

# The proof corpus IS the fixture: adding a dataset = adding a row.
CORPUS = [
    ("examples/datasets/ls_static.yml", 1, 9),
    ("examples/datasets/broad_sigma.yml", 10000, 10000),
    ("examples/datasets/cncs_incident_beam.yml", 100, 0),
    ("examples/datasets/challenge.yml", 1, 9),
]


@pytest.mark.parametrize("yaml_rel,n_entities,n_artifacts", CORPUS)
def test_corpus_counts(tmp_path, yaml_rel, n_entities, n_artifacts):
    cfg = load_config(REPO / yaml_rel)
    ent_df, art_df = generate_manifests(cfg, tmp_path / cfg["key"])
    assert len(ent_df) == n_entities
    assert len(art_df) == n_artifacts
    assert list(art_df.columns) == ARTIFACT_COLUMNS
    assert ent_df["uid"].is_unique


# --- source.server_base_dir: the server's view of the data root -------------
#
# The deployed pod mounts the same bytes at a different absolute path than the
# generating host does. 'directory' stays the LOCAL truth (manifest generation
# and client.load Mode-A reads both join against it); server_base_dir overrides
# ONLY the base that reaches data_uri. These run offline: no server, no /sdf.

LOCAL = "/sdf/data/lcls/ds/prj/prjmaiqmag01/results"
SERVER = "/prjmaiqmag01"


def _cfg(**source_extra):
    return {"key": "ls_static", "metadata": {"data_type": "spectra"},
            "source": {"files": {"directory": LOCAL, "pattern": "*.h5",
                                 "params": {"group": "/p", "from": "attrs"}},
                       **source_extra},
            "artifacts": [{"type": "spectrum", "dataset": "/spectra"}]}


class _FakeContainer:
    """Captures the DataSource that _register_artifact would POST."""

    def __init__(self):
        self.data_sources = None

    def new(self, structure_family, data_sources, key, metadata):
        self.data_sources = data_sources


def _emit_uri(directory, file="LS/static/S_52.h5"):
    """The data_uri _register_artifact builds for one artifact row."""
    box = _FakeContainer()
    row = {"type": "spectrum", "dataset": "/spectra", "index": None,
           "shape": "[9, 2048]", "dtype": "float32", "file": file}
    _register_artifact(box, directory, row)
    return box.data_sources[0].assets[0].data_uri


def test_server_base_dir_absent_is_unchanged():
    """THE REGRESSION GUARD: no key == byte-identical to the old behavior."""
    cfg = _cfg()
    assert validate(cfg) == []
    assert server_dir(cfg) == LOCAL
    # Identical to the pre-change expression it replaced.
    assert server_dir(cfg) == cfg["source"][source_tag(cfg)]["directory"]
    assert _emit_uri(server_dir(cfg)) == (
        "file://localhost/sdf/data/lcls/ds/prj/prjmaiqmag01/results/LS/static/S_52.h5")


def test_server_base_dir_set_rebases_uri():
    """With the key set, data_uri carries the SERVER's prefix, same rel file."""
    cfg = _cfg(server_base_dir=SERVER)
    assert validate(cfg) == []
    assert server_dir(cfg) == SERVER
    assert _emit_uri(server_dir(cfg)) == "file://localhost/prjmaiqmag01/LS/static/S_52.h5"
    # The override must not disturb the local truth Mode-A reads depend on.
    assert cfg["source"][source_tag(cfg)]["directory"] == LOCAL


def test_server_base_dir_is_optional_and_must_be_absolute():
    assert validate(_cfg()) == []  # optional: absence is not an error
    errs = validate(_cfg(server_base_dir="prjmaiqmag01"))  # relative -> rejected
    assert any("server_base_dir" in e for e in errs), errs
    assert any("server_base_dir" in e for e in validate(_cfg(server_base_dir=42)))


def test_server_base_dir_does_not_disturb_the_tagged_union():
    """It sits beside the tag; it must not read as a second source tag."""
    assert "exactly one of" not in " ".join(validate(_cfg(server_base_dir=SERVER)))
    assert source_tag(_cfg(server_base_dir=SERVER)) == "files"
    # A genuinely unknown sibling is still rejected.
    assert any("unknown key" in e for e in validate(_cfg(server_bass_dir=SERVER)))


def test_loc_budget():
    total = sum(len(p.read_text().splitlines())
                for p in (REPO / "tilewright").glob("*.py"))
    assert total <= 750, f"OVER BUDGET: tilewright/*.py totals {total} LOC > 750"


def test_contract_concept_budget():
    assert TOP_LEVEL_KEYS == {"key", "metadata", "source", "artifacts"}, (
        "contract concept creep: the allowed top-level YAML keys changed"
    )
