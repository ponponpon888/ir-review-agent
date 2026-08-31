"""Claudeアダプタのテスト。

MessagesClient のフェイクを注入しているので、SDKもAPIキーもネットワークも不要。
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import seg

from ir_review.config import Settings
from ir_review.glossary import Glossary
from ir_review.llm import (
    ClaudeReviewer,
    ClaudeTranslator,
    LLMResponseError,
)
from ir_review.models import Category, FindingSource, Severity


class FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeResponse:
    def __init__(self, text: str, input_tokens: int = 10, output_tokens: int = 5) -> None:
        self.content = [FakeBlock(text)]
        self.usage = FakeUsage(input_tokens, output_tokens)


class FakeClient:
    """用意した応答を順に返すフェイク。例外を混ぜることもできる。"""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("フェイクの応答が尽きました")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


SETTINGS = Settings(anthropic_api_key="dummy", model="test-model")


class TestTranslator:
    def test_returns_translation_text(self) -> None:
        client = FakeClient([FakeResponse("Consolidated net sales increased.")])
        translator = ClaudeTranslator(SETTINGS, client)

        out = translator.translate(
            seg("連結売上高は増加しました。"), "- 連結売上高 => consolidated net sales"
        )

        assert out == "Consolidated net sales increased."

    def test_glossary_is_injected_into_the_prompt(self) -> None:
        client = FakeClient([FakeResponse("ok")])
        translator = ClaudeTranslator(SETTINGS, client)

        translator.translate(
            seg("連結売上高は増加しました。"), "- 連結売上高 => consolidated net sales"
        )

        user_message = client.calls[0]["messages"][0]["content"]
        assert "consolidated net sales" in user_message
        assert "連結売上高は増加しました。" in user_message

    def test_temperature_is_zero(self) -> None:
        """同じ入力から同じ出力を得たいので固定する。"""
        client = FakeClient([FakeResponse("ok")])
        ClaudeTranslator(SETTINGS, client).translate(seg("本文です。"), "")

        assert client.calls[0]["temperature"] == 0

    def test_empty_translation_is_rejected(self) -> None:
        client = FakeClient([FakeResponse("")])
        with pytest.raises(LLMResponseError, match="空"):
            ClaudeTranslator(SETTINGS, client).translate(seg("本文です。"), "")

    def test_transient_api_error_is_retried(self) -> None:
        client = FakeClient([RuntimeError("overloaded"), FakeResponse("Recovered.")])
        translator = ClaudeTranslator(SETTINGS, client, retry_wait_seconds=0)

        assert translator.translate(seg("本文です。"), "") == "Recovered."
        assert len(client.calls) == 2

    def test_retry_is_bounded(self) -> None:
        client = FakeClient([RuntimeError("boom")] * 5)
        translator = ClaudeTranslator(SETTINGS, client, api_retries=2, retry_wait_seconds=0)

        with pytest.raises(LLMResponseError):
            translator.translate(seg("本文です。"), "")
        assert len(client.calls) == 3

    def test_token_usage_is_accumulated(self) -> None:
        """コストを後から確認できるように計上しておく。"""
        client = FakeClient([FakeResponse("a", 100, 20), FakeResponse("b", 50, 10)])
        translator = ClaudeTranslator(SETTINGS, client)

        translator.translate(seg("一つ目です。"), "")
        translator.translate(seg("二つ目です。"), "")

        assert translator.input_tokens == 150
        assert translator.output_tokens == 30


class TestReviewer:
    def test_parses_findings(self) -> None:
        payload = (
            '{"findings":[{"category":"omission","severity":"high",'
            '"message":"前年同期比の記載が欠落しています","excerpt":"Net sales were 100.",'
            '"suggestion":"前年同期比を追加"}]}'
        )
        reviewer = ClaudeReviewer(SETTINGS, FakeClient([FakeResponse(payload)]))

        findings = reviewer.review(seg("売上は前年同期比で増加しました。"), "Net sales were 100.")

        assert len(findings) == 1
        f = findings[0]
        assert f.source is FindingSource.LLM
        assert f.category is Category.OMISSION
        assert f.severity is Severity.HIGH
        assert f.rule_id is None

    def test_empty_findings_is_valid(self) -> None:
        reviewer = ClaudeReviewer(SETTINGS, FakeClient([FakeResponse('{"findings":[]}')]))
        assert reviewer.review(seg("本文です。"), "Text.") == []

    def test_code_fence_is_tolerated(self) -> None:
        """JSONのみと指示していても囲まれて返ることがあるため。"""
        fenced = '```json\n{"findings":[]}\n```'
        reviewer = ClaudeReviewer(SETTINGS, FakeClient([FakeResponse(fenced)]))
        assert reviewer.review(seg("本文です。"), "Text.") == []

    def test_category_outside_llm_scope_is_rejected(self) -> None:
        """決定論的検査の担当領域をLLMが返してきても、型で弾くこと。"""
        payload = (
            '{"findings":[{"category":"number","severity":"high",'
            '"message":"数値が違います","excerpt":"100"}]}'
        )
        client = FakeClient([FakeResponse(payload), FakeResponse(payload)])
        reviewer = ClaudeReviewer(SETTINGS, client, parse_retries=1)

        with pytest.raises(LLMResponseError):
            reviewer.review(seg("本文です。"), "Text.")

    def test_malformed_json_is_retried_with_a_correction(self) -> None:
        client = FakeClient(
            [FakeResponse("すみません、確認しました。"), FakeResponse('{"findings":[]}')]
        )
        reviewer = ClaudeReviewer(SETTINGS, client, parse_retries=1)

        assert reviewer.review(seg("本文です。"), "Text.") == []
        assert len(client.calls) == 2
        assert "JSONのみ" in client.calls[1]["messages"][0]["content"]

    def test_gives_up_after_parse_retries(self) -> None:
        client = FakeClient([FakeResponse("だめな応答")] * 3)
        reviewer = ClaudeReviewer(SETTINGS, client, parse_retries=1)

        with pytest.raises(LLMResponseError, match="解釈できませんでした"):
            reviewer.review(seg("本文です。"), "Text.")

    def test_prompt_tells_the_model_not_to_duplicate_rule_checks(self) -> None:
        """重複した指摘が出ないよう、担当範囲をプロンプトでも狭めていること。"""
        client = FakeClient([FakeResponse('{"findings":[]}')])
        ClaudeReviewer(SETTINGS, client).review(seg("本文です。"), "Text.")

        system = client.calls[0]["system"]
        assert "絶対に指摘しないでください" in system
        assert "用語集" in system


class TestEndToEndWithFakeApi:
    """パイプライン全体を、APIキーなしで一巡させる。

    翻訳 -> 検算 -> 再翻訳 -> レビュー の配線が実際につながっていることの確認。
    """

    def test_full_pipeline_with_retry(self, sample_glossary: Glossary) -> None:
        from ir_review.pipeline import run_review

        segments = [seg("連結売上高は 48,250百万円 となりました。")]

        client = FakeClient(
            [
                # 1回目の翻訳: 指定訳語を使わず、数値も落としている
                FakeResponse("Sales grew."),
                # 再翻訳: 指摘を受けて修正
                FakeResponse("Consolidated net sales were 48,250 million yen."),
                # レビュー: 問題なし
                FakeResponse('{"findings":[]}'),
            ]
        )
        translator = ClaudeTranslator(SETTINGS, client)
        reviewer = ClaudeReviewer(SETTINGS, client)

        result = run_review(
            "doc", segments, sample_glossary, translator=translator, reviewer=reviewer
        )

        assert len(client.calls) == 3
        assert result.translations[0].retried is True
        assert "48,250 million yen" in result.translations[0].translated_text
        # 再翻訳で解消したので、用語集違反も数値不一致も残らない
        assert result.findings == []

    def test_retry_prompt_carries_both_violations(self, sample_glossary: Glossary) -> None:
        """用語集違反と数値不一致の両方が、再翻訳の指示に載ること。"""
        from ir_review.pipeline import run_review

        segments = [seg("連結売上高は 48,250百万円 となりました。")]
        client = FakeClient(
            [
                FakeResponse("Sales grew."),
                FakeResponse("Consolidated net sales were 48,250 million yen."),
                FakeResponse('{"findings":[]}'),
            ]
        )
        run_review(
            "doc",
            segments,
            sample_glossary,
            translator=ClaudeTranslator(SETTINGS, client),
            reviewer=ClaudeReviewer(SETTINGS, client),
        )

        retry_prompt = client.calls[1]["messages"][0]["content"]
        assert "consolidated net sales" in retry_prompt.lower()
        assert "金額・比率が原文と一致しません" in retry_prompt


class TestSdkVersionCompatibility:
    """SDKのバージョン差で文書1本が丸ごと落ちないこと。

    実際に anthropic 1.2.0 で Messages.create() が temperature を受け付けず、
    21セグメント全部が「未処理」になる事故が起きたため、回帰テストとして残す。
    """

    class RejectsTemperature:
        """temperature を渡されると TypeError を投げるクライアント。"""

        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def create(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            if "temperature" in kwargs:
                raise TypeError(
                    "Messages.create() got an unexpected keyword argument 'temperature'"
                )
            return FakeResponse("Translated.")

    def test_falls_back_when_temperature_is_unsupported(self) -> None:
        client = self.RejectsTemperature()
        translator = ClaudeTranslator(SETTINGS, client, retry_wait_seconds=0)

        assert translator.translate(seg("本文です。"), "") == "Translated."
        assert len(client.calls) == 2  # temperature付き -> 失敗 -> 外して成功

    def test_fallback_is_remembered_for_later_calls(self) -> None:
        """毎回1回無駄打ちしないこと。"""
        client = self.RejectsTemperature()
        translator = ClaudeTranslator(SETTINGS, client, retry_wait_seconds=0)

        translator.translate(seg("一つ目です。"), "")
        translator.translate(seg("二つ目です。"), "")

        assert len(client.calls) == 3  # 1回目で2回、2回目以降は1回ずつ
        assert all("temperature" not in c for c in client.calls[1:])

    def test_other_type_errors_are_not_swallowed(self) -> None:
        """temperature 以外の TypeError まで握りつぶさないこと。"""

        class BrokenClient:
            def create(self, **kwargs: Any) -> Any:
                raise TypeError("got an unexpected keyword argument 'model'")

        translator = ClaudeTranslator(SETTINGS, BrokenClient(), retry_wait_seconds=0)
        with pytest.raises(LLMResponseError):
            translator.translate(seg("本文です。"), "")
