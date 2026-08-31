"""パイプラインのテスト。

LLMはすべてフェイクに差し替えているので、APIキーなしで実行でき、結果も決定論的になる。
再翻訳ループが「本当に効いているか」をここで担保する。
"""

from __future__ import annotations

from conftest import seg

from ir_review.config import Settings
from ir_review.glossary import Glossary
from ir_review.models import Category, Finding, FindingSource, Segment, Severity
from ir_review.pipeline import run_review


class ScriptedTranslator:
    """呼ばれるたびに用意した訳文を順に返すフェイク。"""

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[tuple[str, str]] = []

    def translate(self, segment: Segment, glossary_block: str) -> str:
        self.calls.append((segment.id, glossary_block))
        return self.outputs.pop(0) if self.outputs else "(no output)"


class EchoReviewer:
    """必ず1件の指摘を返すフェイク。"""

    def __init__(self) -> None:
        self.calls = 0

    def review(self, segment: Segment, translated_text: str) -> list[Finding]:
        self.calls += 1
        return [
            Finding(
                segment_id=segment.id,
                category=Category.TONE,
                severity=Severity.LOW,
                source=FindingSource.LLM,
                message="トーンの確認",
                excerpt=translated_text[:40],
            )
        ]


def test_runs_without_llm_when_no_translator_given(sample_glossary: Glossary) -> None:
    """APIキーなしでも決定論的検査だけで動くこと。"""
    segments = [seg("営業益は ４２ 百万円 でした。")]
    result = run_review("doc", segments, sample_glossary)

    assert result.translations == []
    assert result.findings
    assert all(f.source is FindingSource.RULE for f in result.findings)


def test_retries_translation_when_glossary_is_violated(sample_glossary: Glossary) -> None:
    """1回目で用語集を破り、2回目で直るケース。指摘は残らず、retried が立つこと。"""
    segments = [seg("連結売上高は増加しました。")]
    translator = ScriptedTranslator(
        [
            "Sales increased.",  # 指定訳語なし -> R007
            "Consolidated net sales increased.",  # 修正済み
        ]
    )

    result = run_review("doc", segments, sample_glossary, translator=translator)

    assert len(translator.calls) == 2
    assert result.translations[0].retried is True
    assert result.translations[0].translated_text == "Consolidated net sales increased."
    assert [f for f in result.findings if f.rule_id == "R007"] == []


def test_retry_prompt_contains_the_actual_violation(sample_glossary: Glossary) -> None:
    """再翻訳では「もう一度」ではなく、何が守られなかったかを渡すこと。"""
    segments = [seg("連結売上高は増加しました。")]
    translator = ScriptedTranslator(["Sales increased.", "Consolidated net sales increased."])

    run_review("doc", segments, sample_glossary, translator=translator)

    retry_block = translator.calls[1][1]
    assert "consolidated net sales" in retry_block.lower()
    assert "前回の訳文には次の問題" in retry_block


def test_violation_survives_when_retry_also_fails(sample_glossary: Glossary) -> None:
    """再翻訳しても直らなければ、指摘として人に返すこと（黙って通さない）。"""
    segments = [seg("連結売上高は増加しました。")]
    translator = ScriptedTranslator(["Sales increased.", "Sales still increased."])

    result = run_review("doc", segments, sample_glossary, translator=translator)

    assert any(f.rule_id == "R007" for f in result.findings)
    assert result.translations[0].retried is True


def test_retry_count_is_bounded(sample_glossary: Glossary) -> None:
    """壊れた翻訳が返り続けても、上限を超えて呼ばないこと。"""
    segments = [seg("連結売上高は増加しました。")]
    translator = ScriptedTranslator(["bad"] * 10)
    settings = Settings(max_translate_retries=2)

    run_review("doc", segments, sample_glossary, settings=settings, translator=translator)

    assert len(translator.calls) == 3  # 初回 + リトライ2回


def test_reviewer_is_called_once_per_segment(sample_glossary: Glossary) -> None:
    segments = [seg("売上は増加しました。", order=0), seg("費用は減少しました。", order=1)]
    translator = ScriptedTranslator(["Sales increased.", "Costs decreased."])
    reviewer = EchoReviewer()

    result = run_review("doc", segments, sample_glossary, translator=translator, reviewer=reviewer)

    assert reviewer.calls == 2
    assert len([f for f in result.findings if f.source is FindingSource.LLM]) == 2


def test_segment_limit_is_enforced(sample_glossary: Glossary) -> None:
    """コストと実行時間の事故を、呼び出し側の善意ではなく設定で止めること。"""
    segments = [seg("売上は増加しました。", order=i) for i in range(20)]
    translator = ScriptedTranslator(["Sales increased."] * 20)
    settings = Settings(max_segments=3)

    result = run_review("doc", segments, sample_glossary, settings=settings, translator=translator)

    assert len(result.segments) == 3
    assert len(translator.calls) == 3


def test_summary_counts(sample_glossary: Glossary) -> None:
    segments = [seg("営業益は ４２ 百万円 でした。")]
    result = run_review("doc", segments, sample_glossary)

    counts = result.counts_by_severity()
    assert sum(counts.values()) == len(result.findings)
    assert result.counts_by_source()["llm"] == 0
