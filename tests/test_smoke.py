"""Offline smoke tests — no server required.

Run from the repo root:

    uv run --with pytest pytest tests/ -v

Four checks are enforced here:
  1. the proof corpus generates exactly the expected entity/artifact counts;
  2. total source LOC in tilewright/ stays <= 750;
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


def test_loc_budget():
    total = sum(len(p.read_text().splitlines())
                for p in (REPO / "tilewright").glob("*.py"))
    assert total <= 750, f"OVER BUDGET: tilewright/*.py totals {total} LOC > 750"


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
