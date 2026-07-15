"""Offline smoke tests — no server required.

Run from the repo root (a host that can see the /sdf proof-corpus data):

    uv run --with pytest pytest tests/ -v

Three budgets are enforced here:
  1. the proof corpus generates exactly the expected entity/artifact counts;
  2. total source LOC in tcb_min/ stays <= 700;
  3. the contract's top-level concept set never grows past 4 keys.
"""

from pathlib import Path

import pytest

from tcb_min.manifest import ARTIFACT_COLUMNS, TOP_LEVEL_KEYS, generate_manifests, load_config

REPO = Path(__file__).resolve().parent.parent

# The proof corpus IS the fixture: adding a dataset = adding a row.
CORPUS = [
    ("datasets/ls_static.yml", 1, 9),
    ("datasets/broad_sigma.yml", 10000, 10000),
    ("datasets/cncs_incident_beam.yml", 100, 0),
]


@pytest.mark.parametrize("yaml_rel,n_entities,n_artifacts", CORPUS)
def test_corpus_counts(tmp_path, yaml_rel, n_entities, n_artifacts):
    cfg = load_config(REPO / yaml_rel)
    ent_df, art_df = generate_manifests(cfg, tmp_path / cfg["key"])
    assert len(ent_df) == n_entities
    assert len(art_df) == n_artifacts
    assert list(art_df.columns) == ARTIFACT_COLUMNS
    assert ent_df["uid"].is_unique


def test_loc_budget():
    total = sum(len(p.read_text().splitlines())
                for p in (REPO / "tcb_min").glob("*.py"))
    assert total <= 700, f"OVER BUDGET: tcb_min/*.py totals {total} LOC > 700"


def test_contract_concept_budget():
    assert TOP_LEVEL_KEYS == {"key", "metadata", "source", "artifacts"}, (
        "contract concept creep: the allowed top-level YAML keys changed"
    )
