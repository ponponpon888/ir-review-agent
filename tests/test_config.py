"""設定と .env 読み込みのテスト。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ir_review.config import Settings, load_env_file


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """テスト間で環境変数が漏れないようにする。"""
    for key in list(os.environ):
        if key.startswith("IR_REVIEW_") or key == "ANTHROPIC_API_KEY":
            monkeypatch.delenv(key, raising=False)


def write_env(tmp_path: Path, content: str, *, encoding: str = "utf-8") -> Path:
    p = tmp_path / ".env"
    p.write_text(content, encoding=encoding)
    return p


class TestLoadEnvFile:
    def test_loads_key_value_pairs(self, tmp_path: Path) -> None:
        p = write_env(tmp_path, "ANTHROPIC_API_KEY=sk-test-123\nIR_REVIEW_MAX_PAGES=3\n")

        loaded = load_env_file(p)

        assert set(loaded) == {"ANTHROPIC_API_KEY", "IR_REVIEW_MAX_PAGES"}
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-test-123"

    def test_missing_file_is_not_an_error(self, tmp_path: Path) -> None:
        """.env が無いのは異常ではない（環境変数だけで動く運用があるため）。"""
        assert load_env_file(tmp_path / "does-not-exist") == []

    def test_bom_does_not_break_the_first_key(self, tmp_path: Path) -> None:
        """Windowsのエディタや PowerShell がBOMを付けることがあるため。"""
        p = write_env(tmp_path, "ANTHROPIC_API_KEY=sk-bom\n", encoding="utf-8-sig")

        load_env_file(p)

        assert os.environ["ANTHROPIC_API_KEY"] == "sk-bom"

    def test_comments_and_blank_lines_are_skipped(self, tmp_path: Path) -> None:
        p = write_env(tmp_path, "# コメント\n\nANTHROPIC_API_KEY=sk-1\n")
        assert load_env_file(p) == ["ANTHROPIC_API_KEY"]

    def test_empty_value_is_skipped(self, tmp_path: Path) -> None:
        """.env.example をそのままコピーしても、空欄が空文字として入らないこと。"""
        p = write_env(tmp_path, "ANTHROPIC_API_KEY=\n")
        assert load_env_file(p) == []
        assert "ANTHROPIC_API_KEY" not in os.environ

    def test_quotes_are_stripped(self, tmp_path: Path) -> None:
        p = write_env(tmp_path, 'ANTHROPIC_API_KEY="sk-quoted"\n')
        load_env_file(p)
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-quoted"

    def test_inline_comment_is_removed_for_unquoted_values(self, tmp_path: Path) -> None:
        p = write_env(tmp_path, "IR_REVIEW_MODEL=some-model # 使用モデル\n")
        load_env_file(p)
        assert os.environ["IR_REVIEW_MODEL"] == "some-model"

    def test_hash_inside_quotes_is_kept(self, tmp_path: Path) -> None:
        """引用符の中の # を消してしまわないこと（キーに # が入りうるため）。"""
        p = write_env(tmp_path, 'ANTHROPIC_API_KEY="sk-with#hash"\n')
        load_env_file(p)
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-with#hash"

    def test_export_prefix_is_tolerated(self, tmp_path: Path) -> None:
        p = write_env(tmp_path, "export ANTHROPIC_API_KEY=sk-exported\n")
        load_env_file(p)
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-exported"

    def test_existing_environment_wins_by_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ホスティング側やCIで設定した値を、残っていた .env が黙って壊さないこと。"""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-environment")
        p = write_env(tmp_path, "ANTHROPIC_API_KEY=sk-from-file\n")

        assert load_env_file(p) == []
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-from-environment"

    def test_empty_environment_variable_does_not_block_the_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """設定に失敗して空文字が残ったシェルで、.env が黙って無視され続けないこと。

        実際に PowerShell で $env:ANTHROPIC_API_KEY を空にしてしまった状態から
        .env を置いても読まれない、という詰まり方をしたため回帰テストとして残す。
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        p = write_env(tmp_path, "ANTHROPIC_API_KEY=sk-from-file\n")

        assert load_env_file(p) == ["ANTHROPIC_API_KEY"]
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-from-file"

    def test_override_is_opt_in(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-environment")
        p = write_env(tmp_path, "ANTHROPIC_API_KEY=sk-from-file\n")

        load_env_file(p, override=True)

        assert os.environ["ANTHROPIC_API_KEY"] == "sk-from-file"

    def test_malformed_lines_are_ignored(self, tmp_path: Path) -> None:
        p = write_env(tmp_path, "これは設定行ではない\nANTHROPIC_API_KEY=sk-ok\n")
        assert load_env_file(p) == ["ANTHROPIC_API_KEY"]


class TestSettings:
    def test_defaults_are_conservative(self) -> None:
        s = Settings.from_env()
        assert s.max_pages == 5
        assert s.max_segments == 60
        assert s.llm_available is False

    def test_reads_limits_from_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IR_REVIEW_MAX_SEGMENTS", "7")
        assert Settings.from_env().max_segments == 7

    def test_non_integer_limit_fails_loudly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """黙ってデフォルトに落とすと、設定したつもりで効いていない状態になるため。"""
        monkeypatch.setenv("IR_REVIEW_MAX_SEGMENTS", "たくさん")
        with pytest.raises(ValueError, match="整数"):
            Settings.from_env()

    def test_llm_available_follows_the_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
        assert Settings.from_env().llm_available is True
