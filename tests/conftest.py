import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import engine                      # noqa: E402
from src.config import load_payer_rules     # noqa: E402
from src.load import load_dataset           # noqa: E402


@pytest.fixture(scope="session")
def dataset():
    return load_dataset()


@pytest.fixture(scope="session")
def rules():
    return load_payer_rules()


@pytest.fixture(scope="session")
def results(dataset, rules):
    return engine.run(dataset, rules)


@pytest.fixture(scope="session")
def by_id(results):
    return {r.claim.claim_id: r for r in results}
