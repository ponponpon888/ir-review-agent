"""ドメインモデル。

このファイルの型がシステム全体の契約になる。
LLMの出力もここで定義した型に必ず落としてから先に進める（生の文字列を引き回さない）。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class Severity(StrEnum):
    """指摘の深刻度。

    HIGH は「開示物としてそのまま出せない」レベルに限定する。
    ここを緩めると人がレポートを読まなくなるため、判定基準はルール側で厳しく持つ。
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FindingSource(StrEnum):
    """その指摘を誰が出したか。

    レポート上で決定論的な検査とLLMの推論を必ず区別するために持つ。
    RULE は再現性があり反証可能、LLM は確率的で人の確認が要る、という前提の違いがあるため。
    """

    RULE = "rule"
    LLM = "llm"


class Category(StrEnum):
    GLOSSARY = "glossary"  # 用語集の訳語・表記が守られていない
    NUMBER = "number"  # 数値の不一致・表記ゆれ
    UNIT = "unit"  # 金額単位の混在
    DATE = "date"  # 年度・和暦西暦の表記ゆれ
    EXPRESSION = "expression"  # 開示物として不適切な断定表現
    OMISSION = "omission"  # 訳抜け
    MEANING = "meaning"  # 文意のズレ
    TONE = "tone"  # トーン・文体
    REVIEW_FAILED = "review_failed"  # LLMレビュー自体が失敗した（人の確認が必要）


class Segment(BaseModel):
    """レビューの最小単位。

    指摘は必ずいずれかのセグメントに紐づける。
    「どこの話か分からない指摘」を構造的に作れないようにするための制約。
    """

    id: str = Field(description="p1-s03 のような安定したID")
    page: int = Field(ge=1)
    order: int = Field(ge=0, description="ページ内での出現順")
    text: str

    @field_validator("text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("segment text must not be blank")
        return v


class GlossaryEntry(BaseModel):
    """用語集の1エントリ。

    ja  : 原文（日本語）での正式表記
    en  : この文書で必ず使う英訳
    aliases      : 原文中で使われがちな揺れ表記（見つけたら ja に寄せるよう指摘する）
    forbidden_en : 使ってはいけない英訳（誤解を招く定訳を潰すために持つ）
    """

    ja: str
    en: str
    aliases: list[str] = Field(default_factory=list)
    forbidden_en: list[str] = Field(default_factory=list)
    note: str | None = None


class Finding(BaseModel):
    """レビュー指摘。

    excerpt を必須にしているのは、人が原文に戻って確認できない指摘を出させないため。
    """

    segment_id: str
    category: Category
    severity: Severity
    source: FindingSource
    message: str
    excerpt: str = Field(description="根拠となる原文の抜粋")
    suggestion: str | None = None
    rule_id: str | None = Field(default=None, description="RULE 由来の場合の規則ID (R001 など)")


class TranslationUnit(BaseModel):
    """1セグメントの翻訳結果。"""

    segment_id: str
    source_text: str
    translated_text: str
    retried: bool = Field(default=False, description="用語集違反により再翻訳したか")


class ReviewResult(BaseModel):
    """パイプライン全体の出力。"""

    document_name: str
    segments: list[Segment]
    findings: list[Finding] = Field(default_factory=list)
    translations: list[TranslationUnit] = Field(default_factory=list)

    @property
    def high_findings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.HIGH]

    def counts_by_severity(self) -> dict[str, int]:
        out = {s.value: 0 for s in Severity}
        for f in self.findings:
            out[f.severity.value] += 1
        return out

    def counts_by_source(self) -> dict[str, int]:
        out = {s.value: 0 for s in FindingSource}
        for f in self.findings:
            out[f.source.value] += 1
        return out
