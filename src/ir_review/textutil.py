"""正規化と数値抽出。

日本語の開示資料は全角/半角、カンマ、単位表記が混在しやすい。
「表記が違うだけで同じ数値」と「本当に違う数値」を切り分けるために、
比較用の正規化をここに集約している。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# 金額単位 -> 倍率
JA_SCALES: dict[str, int] = {
    "千円": 10**3,
    "百万円": 10**6,
    "億円": 10**8,
    "兆円": 10**12,
}

EN_SCALES: dict[str, int] = {
    "thousand": 10**3,
    "million": 10**6,
    "billion": 10**9,
    "trillion": 10**12,
}

_NUMBER_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
_ZENKAKU_DIGIT_RE = re.compile(r"[０-９]")

# 「令和7年」「平成31年」のような和暦
_ERA_RE = re.compile(r"(令和|平成|昭和)\s*(\d+|元)\s*年")
# 「2026年」のような西暦
_SEIREKI_RE = re.compile(r"(?<!\d)(19|20)\d{2}\s*年")


def normalize(text: str) -> str:
    """比較用の正規化。

    NFKC で全角英数と全角記号を半角へ寄せ、連続空白を潰す。
    原文の保持が必要な箇所ではこれを使わず、比較のときだけ通す。
    """
    t = unicodedata.normalize("NFKC", text)
    t = t.replace("　", " ")
    return re.sub(r"[ \t]+", " ", t).strip()


@dataclass(frozen=True)
class NumberToken:
    """文中に現れた数値ひとつ。

    value は単位を掛けた実数値。単位が読めなかった場合は raw のみで比較する。

    financial は「金額・比率として書かれた数値」かどうか。
    金額単位・パーセント・小数点・桁区切りのいずれかを伴う数値を financial とみなす。
    日付や四半期番号のような裸の整数と区別するために持つ（理由は rules.R005 を参照）。
    """

    raw: str
    value: float
    scale: int = 1
    financial: bool = False
    start: int = -1  # 正規化後テキスト上の開始位置（和暦の年数かどうかの判定に使う）

    @property
    def absolute(self) -> float:
        return self.value * self.scale


def _to_float(raw: str) -> float:
    return float(raw.replace(",", ""))


def _is_financial(raw: str, scale: int, tail: str) -> bool:
    if scale != 1:
        return True
    if "," in raw or "." in raw:
        return True
    stripped = tail.lstrip()
    return stripped.startswith("%") or stripped.lower().startswith("percent")


def extract_numbers_ja(text: str) -> list[NumberToken]:
    """日本語文から数値を抽出する。数値の直後に続く金額単位を倍率として拾う。"""
    t = normalize(text)
    out: list[NumberToken] = []
    for m in _NUMBER_RE.finditer(t):
        raw = m.group()
        tail = t[m.end() : m.end() + 12]
        scale = 1
        for unit, mult in JA_SCALES.items():
            if tail.startswith(unit):
                scale = mult
                break
        out.append(
            NumberToken(
                raw=raw,
                value=_to_float(raw),
                scale=scale,
                financial=_is_financial(raw, scale, tail),
                start=m.start(),
            )
        )
    return out


def extract_numbers_en(text: str) -> list[NumberToken]:
    """英文から数値を抽出する。数値の直後の thousand/million/billion を倍率として拾う。"""
    t = normalize(text)
    out: list[NumberToken] = []
    for m in _NUMBER_RE.finditer(t):
        raw = m.group()
        tail = t[m.end() : m.end() + 12]
        scale = 1
        for unit, mult in EN_SCALES.items():
            if tail.lower().lstrip().startswith(unit):
                scale = mult
                break
        out.append(
            NumberToken(
                raw=raw,
                value=_to_float(raw),
                scale=scale,
                financial=_is_financial(raw, scale, tail),
                start=m.start(),
            )
        )
    return out


# 元号 -> その元号の1年に対応する西暦から1を引いた値（令和1年 = 2019 なので 2018）
ERA_BASE_YEAR: dict[str, int] = {"令和": 2018, "平成": 1988, "昭和": 1925}


def era_year_spans(text: str) -> list[tuple[int, int, float]]:
    """和暦の年数について (開始位置, 終了位置, 対応する西暦) を返す。

    位置で返すのは、同じ文に「令和8年」と「8拠点」があったときに、
    後者まで西暦へ読み替えてしまわないため。
    """
    t = normalize(text)
    out: list[tuple[int, int, float]] = []
    for m in _ERA_RE.finditer(t):
        era, digits = m.group(1), m.group(2)
        year = 1 if digits == "元" else int(digits)
        out.append((m.start(2), m.end(2), float(ERA_BASE_YEAR[era] + year)))
    return out


# 英語では日付・序数・小さい数が語で書かれる（3月 -> March、第2四半期 -> Second Quarter）。
# 数字として現れないだけで訳抜けではないので、語の形も数値として認識する。
_EN_MONTHS: dict[str, float] = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}  # fmt: skip

_EN_WORD_NUMBERS: dict[str, float] = {
    "one": 1, "first": 1, "two": 2, "second": 2, "three": 3, "third": 3,
    "four": 4, "fourth": 4, "five": 5, "fifth": 5, "six": 6, "sixth": 6,
    "seven": 7, "seventh": 7, "eight": 8, "eighth": 8, "nine": 9, "ninth": 9,
    "ten": 10, "tenth": 10, "eleven": 11, "eleventh": 11, "twelve": 12, "twelfth": 12,
    "half": 0.5,
}  # fmt: skip

_WORD_RE = re.compile(r"[a-zA-Z]+")


def word_numbers_en(text: str) -> set[float]:
    """英文中の、語で書かれた数値（月名・序数・数詞）を値の集合として返す。"""
    found: set[float] = set()
    for word in _WORD_RE.findall(normalize(text).lower()):
        if word in _EN_MONTHS:
            found.add(_EN_MONTHS[word])
        if word in _EN_WORD_NUMBERS:
            found.add(_EN_WORD_NUMBERS[word])
    return found


def numeric_multiset(tokens: list[NumberToken]) -> dict[float, int]:
    """絶対値ごとの出現回数。数値の集合比較に使う。"""
    counts: dict[float, int] = {}
    for t in tokens:
        counts[t.absolute] = counts.get(t.absolute, 0) + 1
    return counts


def find_zenkaku_digits(text: str) -> list[str]:
    """全角数字をそのまま返す（正規化前の原文に対して使うこと）。"""
    return _ZENKAKU_DIGIT_RE.findall(text)


def find_ja_units(text: str) -> set[str]:
    t = normalize(text)
    return {u for u in JA_SCALES if u in t}


def has_era_notation(text: str) -> bool:
    return bool(_ERA_RE.search(normalize(text)))


def has_seireki_notation(text: str) -> bool:
    return bool(_SEIREKI_RE.search(normalize(text)))


def contains(haystack: str, needle: str) -> bool:
    """正規化したうえでの部分一致。用語集の突合に使う。"""
    return normalize(needle).lower() in normalize(haystack).lower()


def excerpt_around(text: str, needle: str, width: int = 40) -> str:
    """指摘の根拠として見せる抜粋を切り出す。見つからなければ先頭を返す。"""
    t = normalize(text)
    n = normalize(needle)
    idx = t.lower().find(n.lower())
    if idx < 0:
        return t[: width * 2]
    start = max(0, idx - width // 2)
    end = min(len(t), idx + len(n) + width // 2)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(t) else ""
    return f"{prefix}{t[start:end]}{suffix}"
