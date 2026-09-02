from pathlib import Path

import pytest

SYNTHETIC_DIR = Path(__file__).parent / "fixtures" / "synthetic"
REAL_DIR = (
    Path(__file__).parents[1] / "src" / "scrapper" / "sources" / "beforward" / "fixtures"
)


@pytest.fixture
def synthetic():
    def _load(name: str) -> str:
        return (SYNTHETIC_DIR / name).read_text(encoding="utf-8")

    return _load
