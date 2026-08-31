"""外部依存の境界（ポート）。

翻訳とレビューはLLMを呼ぶが、パイプラインはその事実を知らない。
Protocol で切っておくことで、
  - テストは APIキーなしで実行できる（フェイクを注入する）
  - LLMの提供元を差し替えてもパイプラインは変えなくてよい
  - LLMを呼ばずに決定論的検査だけ回す運用ができる
という3つが同時に成り立つ。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import Finding, Segment


@runtime_checkable
class Translator(Protocol):
    """日本語セグメントを英訳する。"""

    def translate(self, segment: Segment, glossary_block: str) -> str:
        """英訳を返す。用語集ブロックは必ずプロンプトに含めること。

        ここで用語集が守られる保証はない。守られたかどうかは rules.py が検算する。
        """
        ...


@runtime_checkable
class Reviewer(Protocol):
    """決定論的検査では取れない観点（文意・訳抜け・トーン）をレビューする。"""

    def review(self, segment: Segment, translated_text: str) -> list[Finding]: ...
