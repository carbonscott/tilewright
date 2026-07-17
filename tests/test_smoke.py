"""Offline smoke tests — no server required.

Run from the repo root:

    uv run --with pytest pytest tests/ -v

Four checks are enforced here:
  1. the proof corpus generates exactly the expected entity/artifact counts;
  2. total source LOC in tilewright/ stays <= 820;
  3. the contract's top-level concept set never grows past 4 keys;
  4. every skill's frontmatter still parses and names its own directory.

Budget 1 reads the real corpus under /sdf, so it runs only where that data is
mounted (e.g. sdfiana025); elsewhere those cases skip — an unmounted
filesystem is not a broken contract. The skip is keyed to the mount, not to
the dataset: where /sdf IS mounted, a missing dataset fails loudly rather than
skipping. Budgets 2 and 3 always run.
"""

from pathlib import Path

import pytest

from tilewright.manifest import (
    ARTIFACT_COLUMNS,
    TOP_LEVEL_KEYS,
    _generate_groups,
    _uid,
    generate_manifests,
    load_config,
    server_dir,
    source_tag,
    validate,
)
from tilewright.register import _register_artifact, register_dataset

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
    directory = Path(cfg["source"][source_tag(cfg)]["directory"])
    # Skip only where the corpus filesystem itself is absent (a laptop, CI). If
    # it IS here, the dataset must be too: a missing directory is then a real
    # regression, so fall through and let generation fail loudly rather than
    # skipping the budget this suite exists to enforce. Note this is the first
    # path component, not a true mount point — a host that has /sdf but lacks
    # the corpus subtree will fail rather than skip, which errs toward noise
    # over silence.
    mount = Path(*directory.parts[:2])  # e.g. /sdf
    if not mount.exists():
        pytest.skip(f"proof-corpus filesystem {mount} not mounted on this host")
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


def _cfg(**tag_extra):
    return {"key": "ls_static", "metadata": {"data_type": "spectra"},
            "source": {"files": {"directory": LOCAL, "pattern": "*.h5",
                                 "params": {"group": "/p", "from": "attrs"},
                                 **tag_extra}},
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


# --- source.groups: many self-contained entities inside ONE file ------------
#
# Issue #5: a producer wrote 20,000 entities into one 19GB HDF5 as /sample_N
# groups, each with its own params/ and its own arrays — nothing stacked on a
# leading axis. 'files' collapses that to a single entity, 'batch' would demand
# rewriting 19GB to stack it, and 'table' is pointer-only (it forbids
# 'artifacts', and forbids server_base_dir besides). These pin the walker that
# reads the layout as the producer actually wrote it.


def _groups_h5(tmp_path, n=3):
    """A miniature of the NiPS3 layout: /sample_N/{params/{Ax,J1a}, data, powder_data}.

    Params are shape=() DATASETS, not attrs: the real file's group attrs are
    empty (verdict on #5), so a walker that reads only attrs finds nothing here.
    /notes and /README are decoys — the pattern must select on group NAME, and a
    top-level DATASET must never become an entity.
    """
    h5py = pytest.importorskip("h5py")
    import numpy as np

    path = tmp_path / "many.h5"
    with h5py.File(path, "w") as f:
        for i in range(1, n + 1):
            g = f.create_group(f"sample_{i}")
            p = g.create_group("params")
            p.create_dataset("Ax", data=np.float32(i * 0.5))
            p.create_dataset("J1a", data=np.float32(i))
            g.create_dataset("data", data=np.arange(20, dtype="float32").reshape(4, 5) + i)
            g.create_dataset("powder_data", data=np.ones((3,), dtype="float64"))
        f.create_group("notes")                        # a group the pattern must not match
        f.create_dataset("README", data=np.zeros(2))   # not a group: not an entity
    return path


def _groups_cfg(tmp_path, **body_extra):
    return {"key": "MANY", "metadata": {"data_type": "simulation"},
            "source": {"groups": {"directory": str(tmp_path), "file": "many.h5",
                                  "pattern": "sample_*",
                                  "params": {"group": "params", "from": "datasets"},
                                  **body_extra}},
            "artifacts": [{"type": "spectrum", "dataset": "data"},
                          {"type": "powder", "dataset": "powder_data"}]}


def test_groups_contract_is_accepted_and_supports_server_base_dir(tmp_path):
    """groups accepts server_base_dir — the structural defect of the workaround.

    source.table forbids it, so the table stopgap cannot express a server view
    differing from the local view: exactly the SLAC symlinked-/sdf case #4 added
    the key for. A source tag that cannot say where the server sees the bytes
    cannot onboard this dataset at all.
    """
    cfg = _groups_cfg(tmp_path)
    assert validate(cfg) == []
    assert source_tag(cfg) == "groups"
    assert validate(_groups_cfg(tmp_path, server_base_dir=SERVER)) == []
    assert server_dir(_groups_cfg(tmp_path, server_base_dir=SERVER)) == SERVER


def test_groups_malformed_contract_names_the_problem_in_domain_language(tmp_path):
    """Every error collected at once, each naming the key an author must fix."""
    body = {k: v for k, v in _groups_cfg(tmp_path)["source"]["groups"].items()
            if k not in ("file", "pattern")}
    errs = validate({"key": "MANY", "metadata": {"data_type": "simulation"},
                     "source": {"groups": body},
                     "artifacts": [{"type": "spectrum", "dataset": "data"}]})
    assert any("source.groups requires 'file'" in e for e in errs), errs
    assert any("source.groups requires 'pattern'" in e for e in errs), errs
    # The predictable author error: writing the pattern as an HDF5 path.
    assert any("group names" in e.lower() for e in
               validate(_groups_cfg(tmp_path, pattern="/sample_*"))), "path-shaped pattern accepted"
    # params rules are the files rules; an unknown sibling is still rejected.
    bad_from = _groups_cfg(tmp_path)
    bad_from["source"]["groups"]["params"] = {"group": "params", "from": "columns"}
    assert any("attrs | datasets" in e for e in validate(bad_from))
    assert any("unknown key" in e for e in validate(_groups_cfg(tmp_path, directoy="/x")))


def test_groups_walker_yields_one_entity_per_group_with_scalar_dataset_params(tmp_path):
    """THE REGRESSION GUARD: one entity PER GROUP, params from shape=() datasets.

    A walker keyed on the file (like 'files') yields 1 entity here, not 3; one
    that reads attrs yields entities with no params at all. Both are the failure
    modes that made this dataset unonboardable.
    """
    _groups_h5(tmp_path)
    ent_rows, _ = _generate_groups(_groups_cfg(tmp_path))
    assert len(ent_rows) == 3, "expected one entity per matching top-level group"
    assert [r["Ax"] for r in ent_rows] == [0.5, 1.0, 1.5]
    assert [r["J1a"] for r in ent_rows] == [1.0, 2.0, 3.0]
    assert isinstance(ent_rows[0]["Ax"], float), "shape=() dataset must land as a plain float"


def test_groups_uid_is_the_documented_file_plus_group_path_provenance(tmp_path):
    """uid provenance (manifest.py:3) extends by one case: 'rel_path:group_path'.

    Pinned to the literal scheme, not recomputed from the walker, because uid is
    the provenance contract: a uid that silently changes re-registers every
    entity under a new key (register.py:80) instead of skipping it as existing.
    """
    _groups_h5(tmp_path)
    ent_rows, art_rows = _generate_groups(_groups_cfg(tmp_path))
    assert ent_rows[0]["uid"] == _uid("many.h5:/sample_1")
    assert len({r["uid"] for r in ent_rows}) == 3, "group path must make each entity distinct"
    assert art_rows[0]["uid"] == ent_rows[0]["uid"], "artifact must hang off its own entity"


def test_groups_artifact_rows_resolve_within_each_entitys_own_group(tmp_path):
    """Artifacts are declared RELATIVE ('data') and emitted ABSOLUTE per entity.

    The row shape mirrors _generate_files exactly — index=None (whole dataset,
    served by LazyHDF5ArrayAdapter) plus shape/dtype captured now, because
    registration never opens HDF5.
    """
    _groups_h5(tmp_path)
    _, art_rows = _generate_groups(_groups_cfg(tmp_path))
    assert len(art_rows) == 6, "3 groups x 2 artifacts"
    first = art_rows[0]
    assert first["dataset"] == "/sample_1/data", "artifact path must be per-entity, not shared"
    assert first["file"] == "many.h5", "every entity lives in the one file"
    assert first["index"] is None
    assert first["shape"] == "[4, 5]" and first["dtype"] == "float32"
    assert art_rows[1]["dataset"] == "/sample_1/powder_data"
    assert art_rows[1]["shape"] == "[3]" and art_rows[1]["dtype"] == "float64"
    assert {r["dataset"] for r in art_rows if r["type"] == "spectrum"} == {
        f"/sample_{i}/data" for i in (1, 2, 3)}


def test_groups_manifests_carry_the_shape_registration_expects(tmp_path):
    """End to end through generate_manifests: the Parquet is unchanged in schema.

    'groups' is additive — it writes the same two files with the same columns,
    so no register.py, adapter, or Parquet-schema change follows from it.
    """
    _groups_h5(tmp_path)
    ent_df, art_df = generate_manifests(_groups_cfg(tmp_path), tmp_path / "out")
    assert len(ent_df) == 3 and len(art_df) == 6
    assert list(art_df.columns) == ARTIFACT_COLUMNS
    assert art_df["index"].isna().all(), "whole-dataset artifacts carry a null index"
    assert ent_df["uid"].is_unique


def test_loc_budget():
    total = sum(len(p.read_text().splitlines())
                for p in (REPO / "tilewright").glob("*.py"))
    assert total <= 820, f"OVER BUDGET: tilewright/*.py totals {total} LOC > 820"


def test_contract_concept_budget():
    assert TOP_LEVEL_KEYS == {"key", "metadata", "source", "artifacts"}, (
        "contract concept creep: the allowed top-level YAML keys changed"
    )


@pytest.mark.parametrize("skill_dir", sorted(p.name for p in (REPO / "skills").iterdir() if p.is_dir()))
def test_skill_frontmatter(skill_dir):
    """A skill's frontmatter is a machine contract, and it is written in prose.

    An unquoted ": " anywhere in the description makes PyYAML read a mapping
    where a string was meant and raise ScannerError, so the skill stops loading
    entirely — a failure no amount of proofreading catches, because the
    sentence still reads correctly.
    """
    yaml = pytest.importorskip("yaml")
    body = (REPO / "skills" / skill_dir / "SKILL.md").read_text()
    assert body.startswith("---\n"), f"{skill_dir}: no YAML frontmatter"
    meta = yaml.safe_load(body.split("---")[1])
    assert {"name", "description", "allowed-tools"} <= set(meta), (
        f"{skill_dir}: frontmatter missing a required key; has {sorted(meta)}"
    )
    assert meta["name"] == skill_dir, (
        f"{skill_dir}: frontmatter name is {meta['name']!r}; it must match the directory"
    )
def test_register_dataset_wires_server_base_into_registration(monkeypatch):
    """The seam no other test defends: server_dir(cfg) -> _register_artifact.

    Reverting register_dataset's `server_base = server_dir(cfg)` to the old
    `cfg["source"][tag]["directory"]` is the exact regression this feature
    exists to prevent, and every other test passes under it — they compose the
    two halves themselves instead of making register_dataset do it.
    """
    import pandas as pd

    seen = []
    monkeypatch.setattr("tilewright.register.from_uri", lambda *a, **k: {})
    monkeypatch.setattr("tilewright.register._register_one_entity",
                        lambda parent, key, server_base, row, arts: seen.append(server_base) or (0, 0, 1, 0))

    class _Client(dict):
        def create_container(self, key, metadata):
            return object()

    monkeypatch.setattr("tilewright.register.from_uri", lambda *a, **k: _Client())
    ent_df = pd.DataFrame([{"uid": "u1"}])
    register_dataset(_cfg(server_base_dir=SERVER), ent_df, ent_df.iloc[0:0], "http://x", "k", max_workers=1)
    assert seen == [SERVER], f"register_dataset passed {seen}, not the server's view {SERVER!r}"
