"""失敗時の挙動のテスト。

「レビュー済みに見えて実は未レビュー」という状態を作らないこと、が確認したい性質。
"""

from __future__ import annotations

from conftest import seg

from ir_review.glossary import Glossary
from ir_review.models import Category, Finding, Segment, Severity
from ir_review.pipeline import run_review


class BrokenTranslator:
    def translate(self, segment: Segment, glossary_block: str) -> str:
        raise RuntimeError("APIが落ちています")


class OkTranslator:
    def translate(self, segment: Segment, glossary_block: str) -> str:
        return "Sales increased."


class FlakyTranslator:
    """初回は成功、再翻訳で落ちる。"""

    def __init__(self) -> None:
        self.calls = 0

    def translate(self, segment: Segment, glossary_block: str) -> str:
        self.calls += 1
        if self.calls == 1:
            return "Sales increased."  # 用語集違反 -> 再翻訳が走る
        raise RuntimeError("再翻訳中にAPIが落ちました")


class BrokenReviewer:
    def review(self, segment: Segment, translated_text: str) -> list[Finding]:
        raise RuntimeError("応答を解釈できません")


def test_translation_failure_is_surfaced_as_a_high_finding(sample_glossary: Glossary) -> None:
    segments = [seg("売上は増加しました。")]

    result = run_review("doc", segments, sample_glossary, translator=BrokenTranslator())

    failures = [f for f in result.findings if f.category is Category.REVIEW_FAILED]
    assert len(failures) == 1
    assert failures[0].severity is Severity.HIGH
    assert "未処理" in failures[0].message
    assert "APIが落ちています" in failures[0].message
    # 失敗したセグメントの訳文は残さない（あるように見せない）
    assert result.translations == []


def test_review_failure_is_surfaced_but_translation_is_kept(sample_glossary: Glossary) -> None:
    segments = [seg("売上は増加しました。")]

    result = run_review(
        "doc",
        segments,
        sample_glossary,
        translator=OkTranslator(),
        reviewer=BrokenReviewer(),
    )

    assert len(result.translations) == 1
    failures = [f for f in result.findings if f.category is Category.REVIEW_FAILED]
    assert len(failures) == 1
    assert failures[0].severity is Severity.HIGH


def test_retry_failure_falls_back_to_the_first_translation(sample_glossary: Glossary) -> None:
    """再翻訳で落ちても、初回の訳文と用語集違反の指摘は残ること。"""
    segments = [seg("連結売上高は増加しました。")]
    translator = FlakyTranslator()

    result = run_review("doc", segments, sample_glossary, translator=translator)

    assert len(result.translations) == 1
    assert result.translations[0].translated_text == "Sales increased."
    assert any(f.rule_id == "R007" for f in result.findings)


def test_one_broken_segment_does_not_stop_the_others(sample_glossary: Glossary) -> None:
    """1件の失敗で全体を止めない。残りはレビューを完了させる。"""

    class HalfBroken:
        def translate(self, segment: Segment, glossary_block: str) -> str:
            if segment.order == 0:
                raise RuntimeError("落ちました")
            return "Costs decreased."

    segments = [seg("売上は増加しました。", order=0), seg("費用は減少しました。", order=1)]

    result = run_review("doc", segments, sample_glossary, translator=HalfBroken())

    assert len(result.translations) == 1
    assert result.translations[0].segment_id == segments[1].id
    assert any(f.category is Category.REVIEW_FAILED for f in result.findings)
