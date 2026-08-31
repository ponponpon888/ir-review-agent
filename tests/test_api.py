"""HTTP API のテスト。

LLMは使わないので、APIキーもネットワークも不要。
公開デモで効いてほしい防御（認証・レート制限・サイズ上限・同時実行）を中心に固める。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ir_review.api import MAX_UPLOAD_BYTES, create_app
from ir_review.config import Settings
from ir_review.glossary import Glossary

SAMPLE = "連結売上高は 48,250百万円 となりました。営業益は増加しています。"


def build_client(**overrides: object) -> TestClient:
    settings = Settings(**{"rate_limit_per_hour": 0, **overrides})  # type: ignore[arg-type]
    glossary = Glossary.from_yaml(Path(__file__).resolve().parents[1] / "data/glossary.sample.yaml")
    return TestClient(create_app(settings, glossary))


@pytest.fixture
def client() -> Iterator[TestClient]:
    with build_client() as c:
        yield c


def upload(client: TestClient, content: str = SAMPLE, name: str = "doc.md", **kw: object):
    return client.post(
        "/api/review",
        files={"file": (name, content.encode("utf-8"), "text/markdown")},
        data={"translate": "false"},
        **kw,  # type: ignore[arg-type]
    )


def wait_for(client: TestClient, job_id: str, *, timeout: float = 5.0) -> dict:
    """ジョブが終わるまで待つ。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in {"done", "error"}:
            return body
        time.sleep(0.02)
    raise AssertionError("ジョブが完了しませんでした")


class TestBasics:
    def test_index_is_served(self, client: TestClient) -> None:
        res = client.get("/")
        assert res.status_code == 200
        assert "レビュー・翻訳エージェント" in res.text

    def test_healthz_reports_configuration(self, client: TestClient) -> None:
        body = client.get("/healthz").json()
        assert body["status"] == "ok"
        assert body["llm_available"] is False
        assert body["auth_required"] is False
        assert body["max_segments"] == 60

    def test_review_runs_deterministic_checks(self, client: TestClient) -> None:
        res = upload(client)
        assert res.status_code == 202

        job = wait_for(client, res.json()["job_id"])
        assert job["status"] == "done"
        rule_ids = {f["rule_id"] for f in job["result"]["findings"]}
        assert "R004" in rule_ids  # 「営業益」の表記ゆれ
        assert job["summary"]["by_source"]["llm"] == 0

    def test_unknown_job_is_404(self, client: TestClient) -> None:
        assert client.get("/api/jobs/does-not-exist").status_code == 404


class TestUploadValidation:
    def test_unsupported_extension_is_rejected(self, client: TestClient) -> None:
        res = upload(client, name="doc.docx")
        assert res.status_code == 400
        assert "対応していない形式" in res.json()["detail"]

    def test_empty_file_is_rejected(self, client: TestClient) -> None:
        res = upload(client, content="")
        assert res.status_code == 400

    def test_oversized_file_is_rejected(self, client: TestClient) -> None:
        """全部読み込んでから判定すると先にメモリを取られるので、途中で止めること。"""
        big = "あ" * (MAX_UPLOAD_BYTES + 1024)
        res = upload(client, content=big)
        assert res.status_code == 413

    def test_translate_without_api_key_is_rejected(self, client: TestClient) -> None:
        """キーが無いのに翻訳を要求されたら、黙って検査だけ返さず明示的に断ること。"""
        res = client.post(
            "/api/review",
            files={"file": ("doc.md", SAMPLE.encode("utf-8"), "text/markdown")},
            data={"translate": "true"},
        )
        assert res.status_code == 503


class TestAccessKey:
    def test_requests_without_key_are_rejected(self) -> None:
        with build_client(access_key="secret") as client:
            assert upload(client).status_code == 401

    def test_wrong_key_is_rejected(self) -> None:
        with build_client(access_key="secret") as client:
            assert upload(client, headers={"X-Access-Key": "wrong"}).status_code == 401

    def test_correct_key_is_accepted(self) -> None:
        with build_client(access_key="secret") as client:
            assert upload(client, headers={"X-Access-Key": "secret"}).status_code == 202

    def test_job_status_also_requires_the_key(self) -> None:
        """結果の取得口だけ開いていては意味がないこと。"""
        with build_client(access_key="secret") as client:
            res = upload(client, headers={"X-Access-Key": "secret"})
            job_id = res.json()["job_id"]
            assert client.get(f"/api/jobs/{job_id}").status_code == 401

    def test_healthz_reports_that_auth_is_required(self) -> None:
        with build_client(access_key="secret") as client:
            assert client.get("/healthz").json()["auth_required"] is True


class TestRateLimit:
    def test_requests_beyond_the_limit_are_rejected(self) -> None:
        with build_client(rate_limit_per_hour=2) as client:
            first = upload(client)
            wait_for(client, first.json()["job_id"])
            second = upload(client)
            wait_for(client, second.json()["job_id"])

            third = upload(client)
            assert third.status_code == 429
            assert "Retry-After" in third.headers

    def test_zero_means_unlimited(self) -> None:
        """ローカル実行で自分の作業が止まらないようにするため。"""
        with build_client(rate_limit_per_hour=0) as client:
            for _ in range(5):
                res = upload(client)
                assert res.status_code == 202
                wait_for(client, res.json()["job_id"])


class TestConcurrency:
    def test_second_job_is_rejected_while_one_is_running(self, monkeypatch) -> None:
        """同時実行を許すとAPIコストと実行時間が読めなくなるので、受付で断ること。"""
        import ir_review.api as api_module

        def slow_load(path, *, max_pages=None):  # type: ignore[no-untyped-def]
            time.sleep(0.5)
            return []

        monkeypatch.setattr(api_module, "load_document", slow_load)

        with build_client() as client:
            first = upload(client)
            assert first.status_code == 202
            second = upload(client)
            assert second.status_code == 429
            assert "処理中" in second.json()["detail"]


class TestFailureHandling:
    def test_extraction_failure_becomes_an_error_job(self, monkeypatch) -> None:
        """画像PDFなど本文が取れない場合に、空の成功として返さないこと。"""
        import ir_review.api as api_module

        monkeypatch.setattr(api_module, "load_document", lambda p, **kw: [])

        with build_client() as client:
            res = upload(client)
            job = wait_for(client, res.json()["job_id"])
            assert job["status"] == "error"
            assert "抽出できませんでした" in job["error"]

    def test_uploaded_file_is_deleted_after_processing(self, client: TestClient) -> None:
        """預かったファイルをサーバーに残さないこと。"""
        import ir_review.api as api_module

        saved: list[Path] = []
        original = api_module._save_upload

        def spy(upload_file):  # type: ignore[no-untyped-def]
            path = original(upload_file)
            saved.append(path)
            return path

        api_module._save_upload = spy  # type: ignore[assignment]
        try:
            res = upload(client)
            wait_for(client, res.json()["job_id"])
        finally:
            api_module._save_upload = original  # type: ignore[assignment]

        assert saved and not saved[0].exists()
