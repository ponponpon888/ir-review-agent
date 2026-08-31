"""文書の読み込みとセグメント分割。

PDFとテキスト/Markdownの両方を同じ Segment 列に落とす。
以降の処理は入力形式を知らなくてよい。
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import Segment

# 日本語の文末。括弧内の句点で切らないように、閉じ括弧が続く場合は含めて切る。
_SENTENCE_END = re.compile(r"(?<=[。！？])(?![」』）\)])")

# 見出し記号や表の残骸が1セグメントとして混ざるのを防ぐための下限。
# 日本語の意味のある一文はこれより長い。
MIN_SEGMENT_CHARS = 4


def split_into_segments(text: str, page: int = 1, start_order: int = 0) -> list[Segment]:
    """1ページ分のテキストをセグメントへ分割する。

    まず空行で段落に切り、長い段落だけ文単位へ落とす。
    見出しや表の行が1文字ずつ刻まれないように、短い断片は捨てる。
    """
    segments: list[Segment] = []
    order = start_order
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        pieces = [p.strip() for p in _SENTENCE_END.split(block.replace("\n", ""))]
        for piece in pieces:
            if len(piece) < MIN_SEGMENT_CHARS:
                continue
            segments.append(Segment(id=f"p{page}-s{order:03d}", page=page, order=order, text=piece))
            order += 1
    return segments


def load_text(path: str | Path) -> list[Segment]:
    """.txt / .md を読み込む（1ページ扱い）。"""
    content = Path(path).read_text(encoding="utf-8")
    return split_into_segments(content, page=1)


def load_pdf(path: str | Path, *, max_pages: int | None = None) -> list[Segment]:
    """PDFを読み込む。

    max_pages は公開デモでの暴走とコストを抑えるための上限。
    呼び出し側の善意ではなくライブラリ側で止める。
    """
    import pdfplumber  # 遅延インポート（テキスト経路では不要なため）

    segments: list[Segment] = []
    with pdfplumber.open(str(path)) as pdf:
        pages = pdf.pages if max_pages is None else pdf.pages[:max_pages]
        for i, page in enumerate(pages, start=1):
            text = page.extract_text() or ""
            if not text.strip():
                continue
            segments += split_into_segments(text, page=i)
    return segments


def load_document(path: str | Path, *, max_pages: int | None = None) -> list[Segment]:
    """拡張子で読み込み方法を振り分ける。"""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        return load_pdf(p, max_pages=max_pages)
    if suffix in {".txt", ".md", ".markdown"}:
        return load_text(p)
    raise ValueError(f"対応していない拡張子です: {suffix}（.pdf / .txt / .md）")
