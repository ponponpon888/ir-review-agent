from __future__ import annotations

from pathlib import Path

import pytest

from ir_review.extract import load_document, split_into_segments


def test_splits_paragraphs_and_sentences() -> None:
    text = "第一文です。第二文です。\n\n別の段落です。"
    segments = split_into_segments(text)
    assert [s.text for s in segments] == ["第一文です。", "第二文です。", "別の段落です。"]


def test_segment_ids_are_stable_and_ordered() -> None:
    segments = split_into_segments("一つ目です。二つ目です。", page=2)
    assert [s.id for s in segments] == ["p2-s000", "p2-s001"]
    assert all(s.page == 2 for s in segments)


def test_does_not_split_inside_closing_bracket() -> None:
    segments = split_into_segments("「達成しました。」と述べています。")
    assert len(segments) == 1


def test_drops_fragments_that_are_too_short() -> None:
    segments = split_into_segments("あ。有効な本文です。")
    assert [s.text for s in segments] == ["有効な本文です。"]


def test_load_document_reads_markdown(sample_document_path: Path) -> None:
    segments = load_document(sample_document_path)
    assert len(segments) > 10
    assert any("連結売上高" in s.text for s in segments)


def test_load_document_rejects_unknown_extension(tmp_path: Path) -> None:
    p = tmp_path / "data.docx"
    p.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="対応していない拡張子"):
        load_document(p)
