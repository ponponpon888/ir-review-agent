"""Claude API のアダプタ。

ports.Translator / ports.Reviewer の実装。ここだけが Anthropic SDK を知っている。

設計上の要点は3つ。

1. LLMに担当させる範囲をプロンプトで明示的に狭めている。
   数値・用語集・表記ゆれは rules.py が決定論的に検査済みなので、
   レビュープロンプトでは「それらは指摘するな」と明示する。
   同じ指摘が二重に出るとレポートの信頼度が下がり、トークンも無駄になるため。

2. 出力を必ず型に落とす。
   レビューはJSONで受け取り Pydantic で検証する。検証に通らないものは結果として採用しない。

3. 失敗を握りつぶさない。
   レビューが最終的に失敗したセグメントは、黙って飛ばさずに
   「レビューできなかった」という指摘として残す（pipeline 側で処理）。
   レビュー済みに見えて実は未レビュー、という状態を作らないための判断。
"""

from __future__ import annotations

import re
import time
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ValidationError

from .config import Settings
from .models import Category, Finding, FindingSource, Segment, Severity

TRANSLATION_SYSTEM_PROMPT = """\
あなたはIR開示資料を日本語から英語へ翻訳する専門の翻訳者です。

次の制約を必ず守ってください。

1. 与えられた用語集の訳語を必ず使用する。使用禁止と指定された訳語は使わない。
2. 数値・単位・年度を改変しない。原文にある数値はすべて訳文に含め、原文にない数値を足さない。
   金額単位は原文の単位をそのまま英語表記にする（百万円 -> million yen）。桁を勝手に換算しない。
3. 意訳・要約・補足をしない。原文にない情報を足さず、原文にある情報を落とさない。
4. 開示資料としての中立的で事務的なトーンを保つ。断定を強めたり弱めたりしない。
5. 出力は英訳の本文のみ。前置き、注釈、コードブロックは一切付けない。
"""

REVIEW_SYSTEM_PROMPT = """\
あなたはIR開示資料の日英翻訳をレビューする校閲者です。

重要: 次の項目は別途プログラムによる決定論的な検査で既にチェック済みです。
これらは絶対に指摘しないでください。重複した指摘はレポートの価値を下げます。

- 数値の不一致、単位の換算、桁の誤り
- 用語集で指定された訳語が使われているか
- 表記ゆれ（全角数字、和暦西暦、金額単位の混在）

あなたが見るのは、プログラムでは判定できない次の3点だけです。

- omission : 原文にある情報が訳文で欠落している（訳抜け）
- meaning  : 訳文が原文と異なる意味になっている、または誤解を招く
- tone     : 開示資料として不適切なトーン（過度に宣伝的、口語的、断定が強すぎる 等）

問題がなければ findings を空配列で返してください。
無理に指摘を作らないこと。確信が持てないものは返さないこと。

出力は次の形式のJSONのみ。前置きやコードブロックは付けないでください。

{
  "findings": [
    {
      "category": "omission" | "meaning" | "tone",
      "severity": "high" | "medium" | "low",
      "message": "何が問題かを日本語で簡潔に",
      "excerpt": "根拠となる訳文の該当箇所をそのまま抜粋",
      "suggestion": "修正案（任意、不要なら null）"
    }
  ]
}
"""


class LLMResponseError(RuntimeError):
    """LLMの応答を所定の型に落とせなかった。"""


class MessagesClient(Protocol):
    """Anthropic SDK のうち、このモジュールが使う部分だけの型。

    テストではこの形のフェイクを渡すので、SDKもAPIキーも不要になる。
    """

    def create(self, **kwargs: Any) -> Any: ...


def _default_client(settings: Settings) -> MessagesClient:
    import anthropic  # 遅延インポート（LLMを使わない経路では不要なため）

    if not settings.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY が設定されていません")
    return cast(MessagesClient, anthropic.Anthropic(api_key=settings.anthropic_api_key).messages)


def _text_of(response: Any) -> str:
    """Messages API のレスポンスからテキストを取り出す。"""
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            parts.append(str(text))
    return "".join(parts).strip()


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _strip_fence(text: str) -> str:
    """指示していてもコードブロックで返ってくることがあるため、保険で外す。"""
    return _FENCE_RE.sub("", text).strip()


class _BaseClaude:
    """リトライとトークン計上の共通部分。"""

    def __init__(
        self,
        settings: Settings,
        client: MessagesClient | None = None,
        *,
        api_retries: int = 2,
        retry_wait_seconds: float = 1.0,
    ) -> None:
        self.settings = settings
        self._client = client if client is not None else _default_client(settings)
        self._api_retries = api_retries
        self._retry_wait = retry_wait_seconds
        self.input_tokens = 0
        self.output_tokens = 0
        # temperature を受け付けないSDKバージョンがあるため、初回の応答で判定して以降は外す
        self._supports_temperature = True

    def _invoke(self, **kwargs: Any) -> Any:
        """SDKのバージョン差を吸収して呼び出す。

        temperature=0 を指定したいのは、同じ入力から同じ出力を得たいため。
        ただし引数として受け付けないSDKバージョンがあり、そこで全セグメントが落ちると
        文書1本まるごと未処理になる。指定できない環境では黙って外して処理を続ける。
        """
        if self._supports_temperature:
            try:
                return self._client.create(temperature=0, **kwargs)
            except TypeError as exc:
                if "temperature" not in str(exc):
                    raise
                self._supports_temperature = False
        return self._client.create(**kwargs)

    def _call(self, *, system: str, user: str, max_tokens: int) -> str:
        """API呼び出し。一時的な失敗のみリトライする。"""
        last_error: Exception | None = None
        for attempt in range(self._api_retries + 1):
            try:
                response = self._invoke(
                    model=self.settings.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
            except Exception as exc:  # noqa: BLE001 - SDKの例外型に依存しないため
                last_error = exc
                if attempt >= self._api_retries:
                    break
                time.sleep(self._retry_wait * (2**attempt))
                continue

            usage = getattr(response, "usage", None)
            if usage is not None:
                self.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
                self.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
            return _text_of(response)

        raise LLMResponseError(f"API呼び出しに失敗しました: {last_error}") from last_error


class ClaudeTranslator(_BaseClaude):
    """用語集を注入して日→英翻訳する。

    ここで用語集が守られる保証はない。守られたかどうかは rules.py が検算し、
    破られていれば pipeline が違反内容を添えて再依頼する。
    """

    def translate(self, segment: Segment, glossary_block: str) -> str:
        glossary_section = (
            f"# 用語集（必ず従うこと）\n{glossary_block}\n\n"
            if glossary_block.strip()
            else "# 用語集\n（この文に該当する指定用語はありません）\n\n"
        )
        user = f"{glossary_section}# 翻訳対象の原文\n{segment.text}"
        text = self._call(
            system=TRANSLATION_SYSTEM_PROMPT,
            user=user,
            max_tokens=min(2048, max(256, len(segment.text) * 4)),
        )
        if not text:
            raise LLMResponseError(f"{segment.id}: 翻訳結果が空でした")
        return text


class _LLMFinding(BaseModel):
    """LLMが返す1件の指摘。

    category を3種類に限定しているのが要点。
    決定論的検査の担当領域（数値・用語集・表記）をLLMが返しても、
    スキーマ検証の段階で弾かれる。プロンプトの指示だけに頼らず型でも縛っている。
    """

    category: Literal["omission", "meaning", "tone"]
    severity: Literal["high", "medium", "low"]
    message: str
    excerpt: str
    suggestion: str | None = None


class _LLMReviewResponse(BaseModel):
    findings: list[_LLMFinding]


class ClaudeReviewer(_BaseClaude):
    """訳抜け・文意のズレ・トーンだけをレビューする。"""

    def __init__(
        self,
        settings: Settings,
        client: MessagesClient | None = None,
        *,
        api_retries: int = 2,
        parse_retries: int = 1,
        retry_wait_seconds: float = 1.0,
    ) -> None:
        super().__init__(
            settings, client, api_retries=api_retries, retry_wait_seconds=retry_wait_seconds
        )
        self._parse_retries = parse_retries

    def review(self, segment: Segment, translated_text: str) -> list[Finding]:
        user = (
            f"# 原文（日本語）\n{segment.text}\n\n"
            f"# 訳文（英語）\n{translated_text}\n\n"
            "上記を確認し、指定された形式のJSONのみを返してください。"
        )

        last_error: Exception | None = None
        for attempt in range(self._parse_retries + 1):
            raw = self._call(system=REVIEW_SYSTEM_PROMPT, user=user, max_tokens=1024)
            try:
                parsed = _LLMReviewResponse.model_validate_json(_strip_fence(raw))
            except (ValidationError, ValueError) as exc:
                last_error = exc
                if attempt >= self._parse_retries:
                    break
                # 2回目は何が悪かったかを添えて再依頼する
                user += (
                    "\n\n前回の応答は所定のJSON形式として解釈できませんでした。"
                    "説明を付けず、JSONのみを返してください。"
                )
                continue

            return [
                Finding(
                    segment_id=segment.id,
                    category=Category(f.category),
                    severity=Severity(f.severity),
                    source=FindingSource.LLM,
                    message=f.message,
                    excerpt=f.excerpt or translated_text[:120],
                    suggestion=f.suggestion,
                )
                for f in parsed.findings
            ]

        raise LLMResponseError(f"{segment.id}: レビュー応答を解釈できませんでした: {last_error}")
