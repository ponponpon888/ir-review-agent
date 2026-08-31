from __future__ import annotations

from conftest import seg

from ir_review.glossary import Glossary
from ir_review.models import Category, FindingSource, Severity, TranslationUnit
from ir_review.rules import (
    check_era_consistency,
    check_forbidden_expressions,
    check_glossary_aliases,
    check_number_parity,
    check_translation_glossary,
    check_unit_consistency,
    check_zenkaku_digits,
    run_source_rules,
)


class TestZenkakuDigits:
    def test_detects_fullwidth_digits(self) -> None:
        findings = check_zenkaku_digits(seg("自己資本比率は ４２.１% です。"))
        assert len(findings) == 1
        assert findings[0].rule_id == "R001"
        assert findings[0].severity is Severity.LOW

    def test_clean_text_produces_nothing(self) -> None:
        assert check_zenkaku_digits(seg("自己資本比率は 42.1% です。")) == []


class TestForbiddenExpressions:
    def test_forward_looking_assertion_is_high(self) -> None:
        findings = check_forbidden_expressions(seg("必ず黒字化を達成する見込みであります。"))
        assert len(findings) == 1
        assert findings[0].severity is Severity.HIGH
        assert findings[0].category is Category.EXPRESSION

    def test_past_fact_assertion_is_only_medium(self) -> None:
        """実績に掛かる断定まで HIGH にするとレポートが読まれなくなるため。"""
        findings = check_forbidden_expressions(seg("当該施策は確実に実施いたしました。"))
        assert len(findings) == 1
        assert findings[0].severity is Severity.MEDIUM

    def test_neutral_text_produces_nothing(self) -> None:
        assert check_forbidden_expressions(seg("黒字化を目指してまいります。")) == []


class TestGlossaryAliases:
    def test_variant_spelling_is_flagged(self, sample_glossary: Glossary) -> None:
        findings = check_glossary_aliases(seg("営業益は増加しました。"), sample_glossary)
        assert len(findings) == 1
        assert findings[0].rule_id == "R004"
        assert "営業利益" in findings[0].message


class TestUnitConsistency:
    def test_mixed_units_are_flagged_once(self) -> None:
        segments = [
            seg("連結売上高は 48,250百万円 です。", order=0),
            seg("有利子負債は 28,300千円 減少しました。", order=1),
        ]
        findings = check_unit_consistency(segments)
        assert len(findings) == 1
        assert findings[0].rule_id == "R002"
        # 後に出てきた側を指摘対象にする
        assert findings[0].segment_id == segments[1].id

    def test_single_unit_is_clean(self) -> None:
        segments = [
            seg("連結売上高は 48,250百万円 です。"),
            seg("営業利益は 5,120百万円 です。", order=1),
        ]
        assert check_unit_consistency(segments) == []


class TestEraConsistency:
    def test_mixed_era_and_gregorian_is_flagged(self) -> None:
        segments = [
            seg("令和8年3月期を最終年度とする計画", order=0),
            seg("2027年3月期の目標", order=1),
        ]
        findings = check_era_consistency(segments)
        assert len(findings) == 1
        assert findings[0].rule_id == "R003"

    def test_gregorian_only_is_clean(self) -> None:
        segments = [seg("2026年3月期", order=0), seg("2027年3月期", order=1)]
        assert check_era_consistency(segments) == []


class TestNumberParity:
    def test_matching_numbers_pass(self) -> None:
        s = seg("営業利益は 5,120百万円 となりました。")
        unit = TranslationUnit(
            segment_id=s.id,
            source_text=s.text,
            translated_text="Operating income was 5,120 million yen.",
        )
        assert check_number_parity(unit, s) == []

    def test_wrong_scale_is_high_severity(self) -> None:
        s = seg("営業利益は 5,120百万円 となりました。")
        unit = TranslationUnit(
            segment_id=s.id,
            source_text=s.text,
            translated_text="Operating income was 5,120 billion yen.",
        )
        findings = check_number_parity(unit, s)
        assert len(findings) == 1
        assert findings[0].severity is Severity.HIGH
        assert findings[0].rule_id == "R005"

    def test_dropped_number_is_detected(self) -> None:
        s = seg("連結売上高は 48,250百万円、前年同期比 7.2% の増収です。")
        unit = TranslationUnit(
            segment_id=s.id,
            source_text=s.text,
            translated_text="Consolidated net sales were 48,250 million yen.",
        )
        findings = check_number_parity(unit, s)
        assert len(findings) == 1
        assert "7.2" in findings[0].message


class TestTranslationGlossary:
    def test_missing_designated_translation_is_flagged(self, sample_glossary: Glossary) -> None:
        s = seg("連結売上高は増加しました。")
        unit = TranslationUnit(
            segment_id=s.id, source_text=s.text, translated_text="Sales increased."
        )
        findings = check_translation_glossary(unit, s, sample_glossary)
        assert any(f.rule_id == "R007" for f in findings)

    def test_forbidden_translation_is_flagged(self, sample_glossary: Glossary) -> None:
        s = seg("連結売上高は増加しました。")
        unit = TranslationUnit(
            segment_id=s.id,
            source_text=s.text,
            translated_text="Consolidated net sales, or consolidated revenue, increased.",
        )
        findings = check_translation_glossary(unit, s, sample_glossary)
        assert any(f.rule_id == "R008" for f in findings)

    def test_correct_translation_passes(self, sample_glossary: Glossary) -> None:
        s = seg("連結売上高は増加しました。")
        unit = TranslationUnit(
            segment_id=s.id,
            source_text=s.text,
            translated_text="Consolidated net sales increased.",
        )
        assert check_translation_glossary(unit, s, sample_glossary) == []

    def test_substring_does_not_trigger_forbidden_term(self, sample_glossary: Glossary) -> None:
        """語境界を見ずに部分一致させると誤検出するため。"""
        s = seg("経常利益は増加しました。")
        unit = TranslationUnit(
            segment_id=s.id,
            source_text=s.text,
            # "recurring profits" ではなく "recurring" を含まない語
            translated_text="Ordinary income increased on a recurring-free basis.",
        )
        findings = check_translation_glossary(unit, s, sample_glossary)
        assert all(f.rule_id != "R008" for f in findings)


def test_all_rule_findings_are_marked_as_rule_source(sample_glossary: Glossary) -> None:
    """レポート上で決定論的検査とLLMの推論を必ず区別できること。"""
    segments = [
        seg("営業益は ４２ 百万円 でした。", order=0),
        seg("必ず達成する見込みです。", order=1),
    ]
    findings = run_source_rules(segments, sample_glossary)
    assert findings
    assert all(f.source is FindingSource.RULE for f in findings)
    assert all(f.rule_id is not None for f in findings)


def test_every_finding_carries_an_excerpt(sample_glossary: Glossary) -> None:
    """人が原文に戻って確認できない指摘を出さないこと。"""
    segments = [seg("営業益は ４２ 百万円 でした。")]
    findings = run_source_rules(segments, sample_glossary)
    assert findings
    assert all(f.excerpt.strip() for f in findings)


class TestNumberParityWithWordNumbers:
    """英語で数値が語になるケース。

    実際に「2026年3月期 第2四半期決算に関するお知らせ」の見出しで、
    3（March）と 2（Second）が訳文に無いと判定される誤検出が出たため、
    回帰テストとして残す。
    """

    def test_title_with_month_and_ordinal_is_not_flagged(self) -> None:
        s = seg("2026年3月期 第2四半期決算に関するお知らせ")
        unit = TranslationUnit(
            segment_id=s.id,
            source_text=s.text,
            translated_text=(
                "Notice Regarding Financial Results for the Second Quarter "
                "of the Fiscal Year Ending March 31, 2026"
            ),
        )
        assert check_number_parity(unit, s) == []

    def test_dates_added_by_the_translation_are_not_flagged(self) -> None:
        """英訳では日付が補われる。訳文側に増えた裸の整数は問題にしない。"""
        s = seg("2026年3月期の業績")
        unit = TranslationUnit(
            segment_id=s.id,
            source_text=s.text,
            translated_text="Results for the fiscal year ending March 31, 2026",
        )
        assert check_number_parity(unit, s) == []

    def test_genuinely_dropped_count_is_still_flagged(self) -> None:
        """語の言い換えを許容しても、本当に落ちた数値は拾うこと。"""
        s = seg("国内10拠点で展開しています。")
        unit = TranslationUnit(
            segment_id=s.id,
            source_text=s.text,
            translated_text="We operate several locations in Japan.",
        )
        findings = check_number_parity(unit, s)
        assert len(findings) == 1
        assert findings[0].rule_id == "R009"
        assert findings[0].severity is Severity.MEDIUM

    def test_spelled_out_count_is_accepted(self) -> None:
        s = seg("国内10拠点で展開しています。")
        unit = TranslationUnit(
            segment_id=s.id,
            source_text=s.text,
            translated_text="We operate ten locations in Japan.",
        )
        assert check_number_parity(unit, s) == []

    def test_money_error_stays_high_even_among_dates(self) -> None:
        """日付を緩めても、金額の桁違いは要修正のまま拾うこと。"""
        s = seg("2026年3月期の営業利益は 5,120百万円 です。")
        unit = TranslationUnit(
            segment_id=s.id,
            source_text=s.text,
            translated_text=(
                "Operating income for the fiscal year ending March 31, 2026 was 5,120 billion yen."
            ),
        )
        findings = check_number_parity(unit, s)
        assert [f.rule_id for f in findings] == ["R005"]
        assert findings[0].severity is Severity.HIGH

    def test_percentage_is_treated_as_financial(self) -> None:
        s = seg("前年同期比 7.2% の増収です。")
        unit = TranslationUnit(
            segment_id=s.id,
            source_text=s.text,
            translated_text="Revenue increased year on year.",
        )
        findings = check_number_parity(unit, s)
        assert findings[0].rule_id == "R005"
        assert findings[0].severity is Severity.HIGH


class TestEraYearConversion:
    """和暦の年数は西暦へ換算してから照合する。"""

    def test_converted_era_year_matches_the_translation(self) -> None:
        s = seg("令和8年3月期を最終年度とする中期経営計画")
        unit = TranslationUnit(
            segment_id=s.id,
            source_text=s.text,
            translated_text="Medium-term management plan ending in March 2026",
        )
        assert check_number_parity(unit, s) == []

    def test_wrong_conversion_is_detected(self) -> None:
        """換算を無視せず突き合わせるので、変換誤りを拾えること。"""
        s = seg("令和8年3月期を最終年度とする中期経営計画")
        unit = TranslationUnit(
            segment_id=s.id,
            source_text=s.text,
            translated_text="Medium-term management plan ending in March 2025",
        )
        findings = check_number_parity(unit, s)
        assert len(findings) == 1
        assert findings[0].rule_id == "R009"
        assert "2026" in findings[0].message
        assert "和暦" in findings[0].message

    def test_gannen_is_handled(self) -> None:
        s = seg("令和元年に設立しました。")
        unit = TranslationUnit(
            segment_id=s.id, source_text=s.text, translated_text="Founded in 2019."
        )
        assert check_number_parity(unit, s) == []

    def test_heisei_is_handled(self) -> None:
        s = seg("平成31年3月期の実績")
        unit = TranslationUnit(
            segment_id=s.id,
            source_text=s.text,
            translated_text="Results for the fiscal year ended March 2019",
        )
        assert check_number_parity(unit, s) == []

    def test_unrelated_number_is_not_converted(self) -> None:
        """同じ文の「8拠点」まで西暦に読み替えてしまわないこと。"""
        s = seg("令和8年3月期に8拠点へ拡大しました。")
        unit = TranslationUnit(
            segment_id=s.id,
            source_text=s.text,
            translated_text="Expanded in the fiscal year ending March 2026.",
        )
        findings = check_number_parity(unit, s)
        assert len(findings) == 1
        assert "8" in findings[0].message  # 拠点数の8は未確認として残る
