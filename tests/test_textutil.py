from __future__ import annotations

import pytest

from ir_review.textutil import (
    excerpt_around,
    extract_numbers_en,
    extract_numbers_ja,
    find_zenkaku_digits,
    has_era_notation,
    has_seireki_notation,
    normalize,
    numeric_multiset,
    word_numbers_en,
)


def test_normalize_converts_fullwidth_and_collapses_space() -> None:
    assert normalize("４２.１％　です") == "42.1% です"


def test_extract_numbers_ja_applies_currency_scale() -> None:
    tokens = extract_numbers_ja("連結売上高は 48,250百万円 となりました。")
    assert len(tokens) == 1
    assert tokens[0].raw == "48,250"
    assert tokens[0].absolute == pytest.approx(48_250 * 10**6)


def test_extract_numbers_ja_without_unit_keeps_raw_value() -> None:
    tokens = extract_numbers_ja("前年同期比で 7.2% の増収")
    assert tokens[0].absolute == pytest.approx(7.2)


def test_extract_numbers_en_applies_scale() -> None:
    tokens = extract_numbers_en("Consolidated net sales were 48,250 million yen.")
    assert tokens[0].absolute == pytest.approx(48_250 * 10**6)


def test_ja_and_en_agree_after_scaling() -> None:
    ja = numeric_multiset(extract_numbers_ja("営業利益は 5,120百万円 でした。"))
    en = numeric_multiset(extract_numbers_en("Operating income was 5,120 million yen."))
    assert ja == en


def test_ja_and_en_disagree_when_unit_is_wrong() -> None:
    ja = numeric_multiset(extract_numbers_ja("営業利益は 5,120百万円 でした。"))
    en = numeric_multiset(extract_numbers_en("Operating income was 5,120 billion yen."))
    assert ja != en


def test_find_zenkaku_digits() -> None:
    assert find_zenkaku_digits("自己資本比率は ４２.１% です") == ["４", "２", "１"]
    assert find_zenkaku_digits("自己資本比率は 42.1% です") == []


def test_era_and_seireki_detection() -> None:
    assert has_era_notation("令和8年3月期") is True
    assert has_seireki_notation("令和8年3月期") is False
    assert has_seireki_notation("2027年3月期") is True
    assert has_era_notation("2027年3月期") is False


def test_excerpt_around_returns_surrounding_text() -> None:
    text = "当第2四半期における連結売上高は 48,250百万円 となり、前年同期比で増収となりました。"
    out = excerpt_around(text, "48,250", width=10)
    assert "48,250" in out
    assert len(out) < len(text)


def test_excerpt_around_falls_back_when_not_found() -> None:
    assert excerpt_around("短い本文です。", "存在しない語", width=10) == "短い本文です。"


def test_financial_flag_distinguishes_money_from_dates() -> None:
    """金額・比率と、日付や四半期番号のような裸の整数を区別すること。"""
    tokens = {
        t.raw: t.financial for t in extract_numbers_ja("2026年3月期の営業利益は 5,120百万円 です。")
    }
    assert tokens["5,120"] is True
    assert tokens["2026"] is False
    assert tokens["3"] is False


def test_percentage_counts_as_financial() -> None:
    tokens = extract_numbers_ja("前年同期比 7.2% の増収")
    assert tokens[0].financial is True


def test_word_numbers_recognises_months_and_ordinals() -> None:
    found = word_numbers_en("Second Quarter of the Fiscal Year Ending March 31, 2026")
    assert 2 in found  # Second
    assert 3 in found  # March
