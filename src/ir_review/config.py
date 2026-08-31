"""設定。環境変数と .env から読む。

上限値（ページ数・セグメント数）はコストと実行時間の事故を止めるためのもので、
デフォルトを保守的に置いている。公開デモではここが最後の砦になる。

APIキーはコードにもリポジトリにも置かず、環境変数か .env から渡す。
.env は .gitignore 済みで、コミットされない。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

# KEY=VALUE 形式の行（先頭の export は許容する）
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def _parse_env_value(raw: str) -> str:
    """値を1つ取り出す。

    クォートで囲まれていればそのまま中身を採用し、囲まれていなければ
    行末の `# コメント` を落とす。囲まれた値の中の `#` を消してしまわないため、
    この2つを区別している。
    """
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value.split(" #", 1)[0].strip()


def load_env_file(path: str | Path = ".env", *, override: bool = False) -> list[str]:
    """.env を環境変数へ読み込み、読み込んだキー名を返す（値は返さない）。

    - 既定で既存の環境変数を上書きしない。ホスティング側やCIで設定した値を、
      うっかり残っていたローカルのファイルが黙って壊すのを防ぐため。
      ただし「空の環境変数」は設定済みとみなさない。設定に失敗して空文字が残った
      シェルでは、.env を置いても無視され続けて原因が分からなくなるため。
    - utf-8-sig で読むのは、Windows のエディタや PowerShell が付けるBOMで
      1行目のキー名が壊れるのを避けるため（`﻿ANTHROPIC_API_KEY` になる）。
    - ファイルが無いのは異常ではないので、静かに何もしない。
    """
    p = Path(path)
    if not p.is_file():
        return []

    loaded: list[str] = []
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _ENV_LINE.match(line)
        if m is None:
            continue
        key, raw = m.group(1), m.group(2)
        value = _parse_env_value(raw)
        if not value:
            continue
        # 空文字の環境変数は「未設定」として扱う（os.environ.get の真偽で判定する）
        if os.environ.get(key) and not override:
            continue
        os.environ[key] = value
        loaded.append(key)
    return loaded


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} は整数で指定してください（現在の値: {raw!r}）") from exc


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None = None
    model: str = "claude-sonnet-4-5"
    max_pages: int = 5
    max_segments: int = 60
    max_translate_retries: int = 1
    # 公開デモ用。空文字なら認証なし（ローカル実行を想定）
    access_key: str = ""
    rate_limit_per_hour: int = 10

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
            model=os.environ.get("IR_REVIEW_MODEL", cls.model),
            max_pages=_int_env("IR_REVIEW_MAX_PAGES", cls.max_pages),
            max_segments=_int_env("IR_REVIEW_MAX_SEGMENTS", cls.max_segments),
            max_translate_retries=_int_env(
                "IR_REVIEW_MAX_TRANSLATE_RETRIES", cls.max_translate_retries
            ),
            access_key=os.environ.get("IR_REVIEW_ACCESS_KEY", ""),
            rate_limit_per_hour=_int_env("IR_REVIEW_RATE_LIMIT_PER_HOUR", cls.rate_limit_per_hour),
        )

    @property
    def llm_available(self) -> bool:
        return bool(self.anthropic_api_key)
