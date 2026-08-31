from __future__ import annotations

import pytest

from ir_review.glossary import Glossary
from ir_review.models import GlossaryEntry


def test_from_yaml_loads_all_entries(full_glossary: Glossary) -> None:
    assert len(full_glossary) == 10
    entry = full_glossary.get("経常利益")
    assert entry is not None
    assert entry.en == "ordinary income"
    assert "recurring profit" in entry.forbidden_en


def test_duplicate_headwords_are_rejected() -> None:
    with pytest.raises(ValueError, match="重複"):
        Glossary(
            [
                GlossaryEntry(ja="営業利益", en="operating income"),
                GlossaryEntry(ja="営業利益", en="operating profit"),
            ]
        )


def test_matches_in_finds_official_terms(sample_glossary: Glossary) -> None:
    hits = sample_glossary.matches_in("当期の連結売上高と営業利益について")
    assert {e.ja for e in hits} == {"連結売上高", "営業利益"}


def test_alias_hits_report_variant_spellings(sample_glossary: Glossary) -> None:
    hits = sample_glossary.alias_hits_in("営業益は前年同期比で増加しました。")
    assert len(hits) == 1
    entry, alias = hits[0]
    assert entry.ja == "営業利益"
    assert alias == "営業益"


def test_alias_is_not_flagged_when_official_term_is_present(sample_glossary: Glossary) -> None:
    """定義文（正式表記と略記が同居する文）を誤検出しないこと。"""
    text = "営業利益（以下「営業益」という）は増加しました。"
    assert sample_glossary.alias_hits_in(text) == []


def test_prompt_block_includes_forbidden_terms(sample_glossary: Glossary) -> None:
    block = sample_glossary.prompt_block()
    assert "連結売上高 => consolidated net sales" in block
    assert "使用禁止" in block
    assert "consolidated revenue" in block


def test_prompt_block_can_be_narrowed_to_relevant_entries(sample_glossary: Glossary) -> None:
    """関係するエントリだけを渡してトークンを節約できること。"""
    related = sample_glossary.matches_in("営業利益は増加しました。")
    block = sample_glossary.prompt_block(related)
    assert "営業利益" in block
    assert "連結売上高" not in block
