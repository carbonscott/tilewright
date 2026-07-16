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
    source_tag,
)

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
