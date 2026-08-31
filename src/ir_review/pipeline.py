"""オーケストレーション。

処理の順番そのものが設計判断なので、ここに一本化して読めるようにしている。

  1. 抽出          : 文書 -> Segment 列
  2. 原文の検査    : LLM不使用。ここで取れる指摘はLLMに投げない
  3. 翻訳          : 用語集をプロンプトに注入して英訳
  4. 訳文の検算    : 用語集が実際に守られたか、数値が一致するかをコードで確認
  5. 再翻訳        : 4で違反が出たセグメントだけ、違反内容を添えて再翻訳（上限あり）
  6. LLMレビュー   : 2と4で取れないもの（文意・訳抜け・トーン）だけを見る

translator / reviewer を渡さなければ 3〜6 は丸ごと省略され、APIキーなしで 1〜2 が動く。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from .config import Settings
from .glossary import Glossary
from .models import (
    Category,
    Finding,
    FindingSource,
    ReviewResult,
    Segment,
    Severity,
    TranslationUnit,
)
from .ports import Reviewer, Translator
from .rules import run_source_rules, run_translation_rules


def _limit_segments(segments: Sequence[Segment], max_segments: int) -> list[Segment]:
    return list(segments[:max_segments])


def _violations_for(
    unit: TranslationUnit, segments: Sequence[Segment], glossary: Glossary
) -> list[Finding]:
    return run_translation_rules([unit], segments, glossary)


def _failure_finding(segment: Segment, stage: str, error: Exception) -> Finding:
    """処理できなかったセグメントを、指摘として可視化する。

    黙って飛ばすと「レビュー済みに見えて実は未レビュー」という状態ができる。
    IR開示物ではそれが一番危ないので、失敗そのものを要修正として人に返す。
    """
    reason = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
    if len(reason) > 100:
        reason = reason[:100] + "…"
    return Finding(
        segment_id=segment.id,
        category=Category.REVIEW_FAILED,
        severity=Severity.HIGH,
        source=FindingSource.RULE,
        message=f"{stage}に失敗（未処理）: {reason}",
        excerpt=segment.text[:120],
        suggestion="再実行するか、この箇所を手動で確認してください",
    )


def _retry_instruction(violations: list[Finding]) -> str:
    """再翻訳時に添える、違反内容そのものの指示文。

    「もう一度やって」ではなく、何が守られなかったかを具体的に返すことで、
    2回目で直る確率を上げる。
    """
    lines = ["前回の訳文には次の問題がありました。これらを必ず修正してください。"]
    for v in violations:
        lines.append(f"- {v.message}" + (f"（{v.suggestion}）" if v.suggestion else ""))
    return "\n".join(lines)


def run_review(
    document_name: str,
    segments: Sequence[Segment],
    glossary: Glossary,
    *,
    settings: Settings | None = None,
    translator: Translator | None = None,
    reviewer: Reviewer | None = None,
    on_progress: Callable[[int, int, Segment], None] | None = None,
) -> ReviewResult:
    """レビューを実行する。

    on_progress は (完了数, 全体数, 直前に処理したセグメント) で呼ばれる。
    LLMを使うと1文書に数分かかることがあり、無反応の画面はユーザーが異常と区別できないため、
    呼び出し側が進捗を出せるようにしている。表示の形式はここでは決めない。
    """
    settings = settings or Settings()
    target = _limit_segments(segments, settings.max_segments)

    findings: list[Finding] = list(run_source_rules(target, glossary))
    translations: list[TranslationUnit] = []

    if translator is not None:
        total = len(target)
        for index, seg in enumerate(target, start=1):
            related = glossary.matches_in(seg.text)
            block = glossary.prompt_block(related)

            try:
                translated = translator.translate(seg, block)
            except Exception as exc:  # noqa: BLE001 - 実装側の例外型に依存しないため
                findings.append(_failure_finding(seg, "翻訳", exc))
                if on_progress is not None:
                    on_progress(index, total, seg)
                continue

            unit = TranslationUnit(
                segment_id=seg.id, source_text=seg.text, translated_text=translated
            )

            # 検算 -> 違反があれば、違反内容を添えて再翻訳する
            attempts = 0
            violations = _violations_for(unit, target, glossary)
            while violations and attempts < settings.max_translate_retries:
                attempts += 1
                retry_block = f"{block}\n\n{_retry_instruction(violations)}"
                try:
                    retried_text = translator.translate(seg, retry_block)
                except Exception:  # noqa: BLE001 - 再翻訳の失敗は初回訳を採用して続行する
                    break
                unit = TranslationUnit(
                    segment_id=seg.id,
                    source_text=seg.text,
                    translated_text=retried_text,
                    retried=True,
                )
                violations = _violations_for(unit, target, glossary)

            translations.append(unit)
            # 再翻訳しても残った違反だけを指摘として残す
            findings += violations

            if reviewer is not None:
                try:
                    findings += reviewer.review(seg, unit.translated_text)
                except Exception as exc:  # noqa: BLE001 - 実装側の例外型に依存しないため
                    findings.append(_failure_finding(seg, "レビュー", exc))

            if on_progress is not None:
                on_progress(index, total, seg)

    return ReviewResult(
        document_name=document_name,
        segments=target,
        findings=findings,
        translations=translations,
    )
