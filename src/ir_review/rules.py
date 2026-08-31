"""決定論的な検査ルール。

設計の中心。ここに入るのは「機械が確実に判定できるもの」だけで、LLMは一切呼ばない。

理由は3つある。
  1. IR開示物では誤答のコストが高く、数値や用語の一致を確率的な判定に委ねられない。
  2. 同じ入力に対して常に同じ指摘が出る（再現性）ので、指摘そのものをテストできる。
  3. LLMに投げる量が減り、コストと遅延が下がる。

LLM側（review.py）には、ここで取れないもの ―― 文意のズレ、訳抜け、トーン ―― だけを担当させる。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from .glossary import Glossary
from .models import Category, Finding, FindingSource, Segment, Severity, TranslationUnit
from .textutil import (
    NumberToken,
    contains,
    era_year_spans,
    excerpt_around,
    extract_numbers_en,
    extract_numbers_ja,
    find_ja_units,
    find_zenkaku_digits,
    has_era_notation,
    has_seireki_notation,
    normalize,
    numeric_multiset,
    word_numbers_en,
)

# 開示物として断定を避けるべき表現。将来見通しに掛かると特に問題になる。
DEFAULT_FORBIDDEN_EXPRESSIONS: tuple[str, ...] = (
    "必ず",
    "絶対に",
    "確実に",
    "保証します",
    "保証いたします",
    "間違いなく",
    "確実です",
)

_FORWARD_LOOKING_HINTS = ("見込", "予想", "計画", "目標", "予定", "見通し")


def _finding(
    segment: Segment,
    *,
    rule_id: str,
    category: Category,
    severity: Severity,
    message: str,
    excerpt: str,
    suggestion: str | None = None,
) -> Finding:
    return Finding(
        segment_id=segment.id,
        category=category,
        severity=severity,
        source=FindingSource.RULE,
        message=message,
        excerpt=excerpt,
        suggestion=suggestion,
        rule_id=rule_id,
    )


# --- 原文（日本語）に対する検査 -------------------------------------------------


def check_zenkaku_digits(segment: Segment) -> list[Finding]:
    """R001: 全角数字の混入。

    全角と半角が混ざると数値の機械処理と検索が壊れるため、開示前に潰す。
    """
    hits = find_zenkaku_digits(segment.text)
    if not hits:
        return []
    return [
        _finding(
            segment,
            rule_id="R001",
            category=Category.NUMBER,
            severity=Severity.LOW,
            message=f"全角数字が {len(hits)} 文字含まれています（{''.join(sorted(set(hits)))}）",
            excerpt=excerpt_around(segment.text, hits[0]),
            suggestion="半角数字に統一してください",
        )
    ]


def check_forbidden_expressions(
    segment: Segment, expressions: Sequence[str] = DEFAULT_FORBIDDEN_EXPRESSIONS
) -> list[Finding]:
    """R006: 断定的な表現。

    将来見通しを示す語（見込・予想など）と同じ文に現れた場合だけ HIGH に上げる。
    実績値に掛かる「確実に実施した」まで HIGH にするとレポートが読まれなくなるため。
    """
    out: list[Finding] = []
    text = normalize(segment.text)
    forward_looking = any(h in text for h in _FORWARD_LOOKING_HINTS)
    for expr in expressions:
        if expr in text:
            out.append(
                _finding(
                    segment,
                    rule_id="R006",
                    category=Category.EXPRESSION,
                    severity=Severity.HIGH if forward_looking else Severity.MEDIUM,
                    message=(
                        f"断定的な表現「{expr}」が使われています"
                        + ("（将来見通しに関する記述のため要確認）" if forward_looking else "")
                    ),
                    excerpt=excerpt_around(segment.text, expr),
                    suggestion="「〜と見込んでおります」等の表現に置き換えを検討してください",
                )
            )
    return out


def check_glossary_aliases(segment: Segment, glossary: Glossary) -> list[Finding]:
    """R004: 用語集で正式表記が決まっている語の揺れ。"""
    out: list[Finding] = []
    for entry, alias in glossary.alias_hits_in(segment.text):
        out.append(
            _finding(
                segment,
                rule_id="R004",
                category=Category.GLOSSARY,
                severity=Severity.MEDIUM,
                message=f"用語集の正式表記は「{entry.ja}」ですが「{alias}」が使われています",
                excerpt=excerpt_around(segment.text, alias),
                suggestion=f"「{entry.ja}」に統一してください",
            )
        )
    return out


def check_unit_consistency(segments: Sequence[Segment]) -> list[Finding]:
    """R002: 文書全体での金額単位の混在。

    文書単位の検査だが、指摘は「2種類目の単位が最初に現れたセグメント」に紐づける。
    どこを直せばよいか分からない指摘を作らないため。
    """
    seen: dict[str, Segment] = {}
    for seg in segments:
        for unit in sorted(find_ja_units(seg.text)):
            seen.setdefault(unit, seg)
    if len(seen) <= 1:
        return []
    units = sorted(seen)
    # 出現順が後のものを指摘対象にする
    culprit_unit = max(units, key=lambda u: (seen[u].page, seen[u].order))
    seg = seen[culprit_unit]
    return [
        _finding(
            seg,
            rule_id="R002",
            category=Category.UNIT,
            severity=Severity.MEDIUM,
            message=f"金額単位が混在しています（{', '.join(units)}）",
            excerpt=excerpt_around(seg.text, culprit_unit),
            suggestion="文書全体で単位を統一してください",
        )
    ]


def check_era_consistency(segments: Sequence[Segment]) -> list[Finding]:
    """R003: 和暦と西暦の混在。"""
    era_seg = next((s for s in segments if has_era_notation(s.text)), None)
    seireki_seg = next((s for s in segments if has_seireki_notation(s.text)), None)
    if era_seg is None or seireki_seg is None:
        return []
    later = max((era_seg, seireki_seg), key=lambda s: (s.page, s.order))
    return [
        _finding(
            later,
            rule_id="R003",
            category=Category.DATE,
            severity=Severity.MEDIUM,
            message="和暦と西暦が混在しています",
            excerpt=excerpt_around(later.text, "年"),
            suggestion="いずれかに統一してください（英訳の一貫性にも影響します）",
        )
    ]


def run_source_rules(
    segments: Sequence[Segment],
    glossary: Glossary,
    *,
    forbidden_expressions: Sequence[str] = DEFAULT_FORBIDDEN_EXPRESSIONS,
) -> list[Finding]:
    """原文に対する決定論的検査をすべて実行する。"""
    findings: list[Finding] = []
    for seg in segments:
        findings += check_zenkaku_digits(seg)
        findings += check_forbidden_expressions(seg, forbidden_expressions)
        findings += check_glossary_aliases(seg, glossary)
    findings += check_unit_consistency(segments)
    findings += check_era_consistency(segments)
    return findings


# --- 翻訳結果に対する検査 ---------------------------------------------------------


def _fmt(values: Iterable[float]) -> str:
    """指摘に載せる数値の並び。整数は小数点を付けずに見せる。"""
    out: list[str] = []
    for v in sorted(values):
        out.append(str(int(v)) if float(v).is_integer() else str(v))
    return ", ".join(out)


def check_number_parity(unit: TranslationUnit, segment: Segment) -> list[Finding]:
    """R005 / R009: 原文と訳文で数値が対応しているか。

    金額・比率とそれ以外を分けて扱う。理由は誤検出の質が違うため。

    R005（金額・比率／要修正）
        金額単位・パーセント・小数点・桁区切りを伴う数値。単位を掛けた絶対値で厳密に比較する。
        「1,234百万円 -> 1,234 million yen」は一致、「-> 1,234 billion yen」は不一致。
        開示物でここを誤ると影響が大きいので、過不足の両方向を見る。

    R009（日付・序数など／要確認）
        裸の整数。英語では数字ではなく語になる（3月 -> March、第2四半期 -> Second Quarter）ため、
        語で書かれた数値も突き合わせたうえで、原文にあって訳文のどこにも見当たらないものだけを挙げる。
        英訳では日付が補われる（「2026年3月期」-> 「fiscal year ending March 31, 2026」）ので、
        訳文側に増えた数値は問題として扱わない。
        和暦は西暦へ換算してから照合する。単に除外すると「令和8年 -> 2025」のような
        変換誤りを見逃すため、換算した西暦が訳文にあるかどうかで判定する。
    """
    ja_tokens = extract_numbers_ja(unit.source_text)
    en_tokens = extract_numbers_en(unit.translated_text)

    findings: list[Finding] = []

    ja_fin = numeric_multiset([t for t in ja_tokens if t.financial])
    en_fin = numeric_multiset([t for t in en_tokens if t.financial])
    if ja_fin != en_fin:
        missing = {k: v for k, v in ja_fin.items() if en_fin.get(k, 0) < v}
        extra = {k: v for k, v in en_fin.items() if ja_fin.get(k, 0) < v}
        parts: list[str] = []
        if missing:
            parts.append(f"訳文に現れない値: {_fmt(missing)}")
        if extra:
            parts.append(f"原文にない値: {_fmt(extra)}")
        findings.append(
            _finding(
                segment,
                rule_id="R005",
                category=Category.NUMBER,
                severity=Severity.HIGH,
                message="金額・比率が原文と一致しません。" + " / ".join(parts),
                excerpt=unit.translated_text[:160],
                suggestion="単位（百万円/million 等）の変換を含めて確認してください",
            )
        )

    # 和暦は英訳で西暦に変換されるので、換算してから照合する。
    # 単に無視すると変換ミス（令和8年 -> 2025 など）を見逃すため、換算値で突き合わせる。
    eras = era_year_spans(unit.source_text)
    converted: dict[float, float] = {}

    def _comparable(token: NumberToken) -> float:
        for start, end, gregorian in eras:
            if start <= token.start < end:
                converted[gregorian] = token.absolute
                return gregorian
        return token.absolute

    ja_inc = {_comparable(t) for t in ja_tokens if not t.financial}
    en_inc = {t.absolute for t in en_tokens if not t.financial}
    en_inc |= word_numbers_en(unit.translated_text)
    unseen = ja_inc - en_inc
    if unseen:
        labels = [
            f"{_fmt([v])}（和暦 {_fmt([converted[v]])} 年からの換算）"
            if v in converted
            else _fmt([v])
            for v in sorted(unseen)
        ]
        findings.append(
            _finding(
                segment,
                rule_id="R009",
                category=Category.NUMBER,
                severity=Severity.MEDIUM,
                message=f"原文の数値が訳文で確認できません: {', '.join(labels)}",
                excerpt=unit.translated_text[:160],
                suggestion="訳抜けか、語での言い換えか、和暦の換算誤りかを確認してください",
            )
        )

    return findings


def check_translation_glossary(
    unit: TranslationUnit, segment: Segment, glossary: Glossary
) -> list[Finding]:
    """R007/R008: 訳文が用語集に従っているか。

    翻訳時にプロンプトへ用語集を入れているが、それだけでは守られたことにならないので、
    出力側をコードで検算する。LLMの自己申告を根拠にしない、というのがこの関数の存在理由。
    """
    out: list[Finding] = []
    for entry in glossary.matches_in(unit.source_text):
        if not contains(unit.translated_text, entry.en):
            out.append(
                _finding(
                    segment,
                    rule_id="R007",
                    category=Category.GLOSSARY,
                    severity=Severity.HIGH,
                    message=f"「{entry.ja}」の指定訳語「{entry.en}」が訳文に現れていません",
                    excerpt=unit.translated_text[:160],
                    suggestion=f"「{entry.en}」を使用してください",
                )
            )
        for banned in entry.forbidden_en:
            if _contains_word(unit.translated_text, banned):
                out.append(
                    _finding(
                        segment,
                        rule_id="R008",
                        category=Category.GLOSSARY,
                        severity=Severity.HIGH,
                        message=f"使用禁止の訳語「{banned}」が使われています（正: {entry.en}）",
                        excerpt=excerpt_around(unit.translated_text, banned),
                        suggestion=f"「{entry.en}」に置き換えてください",
                    )
                )
    return out


def _contains_word(text: str, word: str) -> bool:
    """英単語の部分一致による誤検出を避けるための語境界つき検索。"""
    return re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text, flags=re.IGNORECASE) is not None


def run_translation_rules(
    units: Iterable[TranslationUnit],
    segments: Sequence[Segment],
    glossary: Glossary,
) -> list[Finding]:
    by_id = {s.id: s for s in segments}
    findings: list[Finding] = []
    for u in units:
        seg = by_id.get(u.segment_id)
        if seg is None:
            continue
        findings += check_number_parity(u, seg)
        findings += check_translation_glossary(u, seg, glossary)
    return findings
