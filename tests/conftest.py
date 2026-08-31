from __future__ import annotations

from pathlib import Path

import pytest

from ir_review.glossary import Glossary
from ir_review.models import GlossaryEntry, Segment

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture
def sample_glossary() -> Glossary:
    return Glossary(
        [
            GlossaryEntry(
                ja="連結売上高",
                en="consolidated net sales",
                aliases=["連結売上"],
                forbidden_en=["consolidated revenue"],
            ),
            GlossaryEntry(ja="営業利益", en="operating income", aliases=["営業益"]),
            GlossaryEntry(
                ja="経常利益",
                en="ordinary income",
                forbidden_en=["recurring profit"],
            ),
        ]
    )


@pytest.fixture
def full_glossary() -> Glossary:
    return Glossary.from_yaml(DATA_DIR / "glossary.sample.yaml")


@pytest.fixture
def sample_document_path() -> Path:
    return DATA_DIR / "sample_disclosure_ja.md"


def seg(text: str, *, page: int = 1, order: int = 0) -> Segment:
    return Segment(id=f"p{page}-s{order:03d}", page=page, order=order, text=text)
