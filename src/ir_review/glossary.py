"""用語集の読み込みと突合。

このモジュールは意図的にLLMを一切使わない。
「この語はこう訳す」という決めごとは、確率的に守られるのではなく必ず守られるべきものなので、
検査は決定論的なコードで行い、LLMには守らせる側の役割だけを持たせる。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import GlossaryEntry
from .textutil import contains, normalize


class Glossary:
    """用語集。ja をキーとして引ける。"""

    def __init__(self, entries: list[GlossaryEntry]) -> None:
        self._entries = entries
        self._by_ja = {normalize(e.ja): e for e in entries}
        if len(self._by_ja) != len(entries):
            dupes = [e.ja for e in entries if list(x.ja for x in entries).count(e.ja) > 1]
            raise ValueError(f"用語集に重複した見出し語があります: {sorted(set(dupes))}")

    @property
    def entries(self) -> list[GlossaryEntry]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, ja: str) -> GlossaryEntry | None:
        return self._by_ja.get(normalize(ja))

    @classmethod
    def from_yaml(cls, path: str | Path) -> Glossary:
        raw: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("用語集YAMLはエントリのリストである必要があります")
        return cls([GlossaryEntry.model_validate(item) for item in raw])

    # -- 突合 -------------------------------------------------------------

    def matches_in(self, text: str) -> list[GlossaryEntry]:
        """本文中に出現する用語集エントリ（正式表記でのヒット）。"""
        return [e for e in self._entries if contains(text, e.ja)]

    def alias_hits_in(self, text: str) -> list[tuple[GlossaryEntry, str]]:
        """本文中に出現した「揺れ表記」を (エントリ, 見つかった別名) で返す。

        正式表記が同じ箇所に既にある場合は揺れとみなさない
        （「連結子会社（以下「子会社」という）」のような定義文を誤検出しないため）。
        """
        hits: list[tuple[GlossaryEntry, str]] = []
        for e in self._entries:
            if contains(text, e.ja):
                continue
            for alias in e.aliases:
                if contains(text, alias):
                    hits.append((e, alias))
                    break
        return hits

    def prompt_block(self, entries: list[GlossaryEntry] | None = None) -> str:
        """翻訳プロンプトに差し込む用語集ブロック。

        全件を毎回入れるとトークンを浪費するので、
        呼び出し側でそのセグメントに関係するエントリだけ渡せるようにしている。
        """
        target = entries if entries is not None else self._entries
        lines: list[str] = []
        for e in target:
            line = f"- {e.ja} => {e.en}"
            if e.forbidden_en:
                line += f"（次の訳語は使用禁止: {', '.join(e.forbidden_en)}）"
            if e.note:
                line += f" ※{e.note}"
            lines.append(line)
        return "\n".join(lines)
