"""コマンドラインインターフェース。

APIキーがなくても `--no-translate` で決定論的検査だけ実行できる。
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from .config import Settings, load_env_file
from .extract import load_document
from .glossary import Glossary
from .models import ReviewResult, Segment, Severity
from .pipeline import run_review
from .report import sort_findings, to_json, to_markdown

app = typer.Typer(add_completion=False, help="IR開示資料のレビュー・翻訳エージェント")
console = Console()

_SEVERITY_STYLE = {Severity.HIGH: "bold red", Severity.MEDIUM: "yellow", Severity.LOW: "dim"}
_SEVERITY_LABEL = {Severity.HIGH: "要修正", Severity.MEDIUM: "要確認", Severity.LOW: "軽微"}


def _print_table(result: ReviewResult) -> None:
    table = Table(title=f"レビュー結果: {result.document_name}")
    table.add_column("重要度", no_wrap=True)
    table.add_column("出所", no_wrap=True)
    table.add_column("規則", no_wrap=True)
    table.add_column("箇所", no_wrap=True)
    table.add_column("内容")

    for f in sort_findings(result.findings):
        table.add_row(
            f"[{_SEVERITY_STYLE[f.severity]}]{_SEVERITY_LABEL[f.severity]}[/]",
            "ルール" if f.source.value == "rule" else "LLM",
            f.rule_id or "-",
            f.segment_id,
            f.message,
        )
    console.print(table)

    counts = result.counts_by_severity()
    console.print(
        f"セグメント {len(result.segments)} 件 / "
        f"要修正 {counts['high']}・要確認 {counts['medium']}・軽微 {counts['low']}"
    )


@app.command()
def review(
    document: Path = typer.Argument(..., exists=True, help="レビュー対象（.pdf / .txt / .md）"),
    glossary_path: Path = typer.Option(
        Path("data/glossary.sample.yaml"), "--glossary", "-g", exists=True, help="用語集YAML"
    ),
    translate: bool = typer.Option(
        False, "--translate/--no-translate", help="英訳とLLMレビューまで行う（APIキーが必要）"
    ),
    json_out: Path | None = typer.Option(None, "--json", help="JSONレポートの出力先"),
    markdown_out: Path | None = typer.Option(None, "--markdown", "-m", help="Markdownの出力先"),
    max_pages: int | None = typer.Option(None, "--max-pages", help="PDFの読み込みページ数上限"),
    max_segments: int | None = typer.Option(
        None, "--max-segments", help="処理するセグメント数の上限（試し実行のコスト抑制に使う）"
    ),
    env_file: Path = typer.Option(Path(".env"), "--env-file", help="APIキー等を書いた .env のパス"),
) -> None:
    """文書をレビューする。"""
    # 既存の環境変数が優先。.env はあれば読む、なければ何もしない。
    loaded = load_env_file(env_file)
    if loaded:
        console.print(f"[dim]{env_file} から {', '.join(loaded)} を読み込みました[/]")

    settings = Settings.from_env()
    overrides: dict[str, int] = {}
    if max_pages is not None:
        overrides["max_pages"] = max_pages
    if max_segments is not None:
        overrides["max_segments"] = max_segments
    if overrides:
        settings = Settings(**{**settings.__dict__, **overrides})

    glossary = Glossary.from_yaml(glossary_path)
    segments = load_document(document, max_pages=settings.max_pages)
    if not segments:
        console.print("[red]本文を抽出できませんでした（画像PDFの可能性があります）[/]")
        raise typer.Exit(code=2)

    translator = None
    reviewer = None
    if translate:
        if not settings.llm_available:
            console.print("[red]ANTHROPIC_API_KEY が設定されていません[/]")
            raise typer.Exit(code=2)
        from .llm import ClaudeReviewer, ClaudeTranslator  # 遅延インポート

        translator = ClaudeTranslator(settings)
        reviewer = ClaudeReviewer(settings)

    if translator is None:
        result = run_review(document.name, segments, glossary, settings=settings)
    else:
        # LLMを使うと1文書に数分かかる。無反応の画面は「固まった」と区別がつかないので、
        # セグメント単位で進捗を出す。
        planned = min(len(segments), settings.max_segments)
        console.print(
            f"[dim]{planned} セグメントを処理します（モデル: {settings.model}）。中断は Ctrl+C[/]"
        )
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task_id = progress.add_task("翻訳とレビュー", total=planned)

            def _on_progress(done: int, total: int, seg: Segment) -> None:
                progress.update(task_id, completed=done, description=f"処理中 {seg.id}")

            result = run_review(
                document.name,
                segments,
                glossary,
                settings=settings,
                translator=translator,
                reviewer=reviewer,
                on_progress=_on_progress,
            )

    _print_table(result)

    # 実際にいくら分のトークンを使ったかを毎回見せる。
    # コストが見えない状態でLLMを回すと、事故に気づくのが請求のときになるため。
    if translator is not None or reviewer is not None:
        used_in = sum(c.input_tokens for c in (translator, reviewer) if c is not None)
        used_out = sum(c.output_tokens for c in (translator, reviewer) if c is not None)
        console.print(f"トークン使用量: 入力 {used_in:,} / 出力 {used_out:,}")

    if markdown_out:
        markdown_out.write_text(to_markdown(result), encoding="utf-8")
        console.print(f"Markdown を書き出しました: {markdown_out}")
    if json_out:
        json_out.write_text(to_json(result), encoding="utf-8")
        console.print(f"JSON を書き出しました: {json_out}")

    # 要修正が残っていれば異常終了する（CIに組み込めるようにするため）
    raise typer.Exit(code=1 if result.high_findings else 0)


@app.command()
def rules() -> None:
    """実装されている決定論的ルールの一覧を表示する。"""
    table = Table(title="決定論的ルール（LLM不使用）")
    table.add_column("ID")
    table.add_column("対象")
    table.add_column("内容")
    for rule_id, target, desc in [
        ("R001", "原文", "全角数字の混入"),
        ("R002", "原文", "金額単位の混在（百万円 / 千円 など）"),
        ("R003", "原文", "和暦と西暦の混在"),
        ("R004", "原文", "用語集の正式表記に対する揺れ"),
        ("R005", "訳文", "金額・比率の不一致（単位換算込み・過不足の両方向）"),
        ("R006", "原文", "断定的な表現（将来見通しに掛かる場合は要修正）"),
        ("R007", "訳文", "用語集の指定訳語が使われていない"),
        ("R008", "訳文", "使用禁止の訳語が使われている"),
        ("R009", "訳文", "日付・序数などの数値が訳文で確認できない（語での言い換えは許容）"),
    ]:
        table.add_row(rule_id, target, desc)
    console.print(table)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="待ち受けアドレス"),
    port: int = typer.Option(8000, "--port", help="待ち受けポート"),
    reload: bool = typer.Option(False, "--reload", help="コード変更時に自動再起動（開発用）"),
) -> None:
    """Web UI と API を起動する。"""
    try:
        import uvicorn
    except ImportError:  # pragma: no cover - 依存を入れていない場合の案内
        console.print('[red]Web機能の依存が入っていません: pip install -e ".[api]"[/]')
        raise typer.Exit(code=2) from None

    load_env_file()
    settings = Settings.from_env()
    console.print(f"http://{host}:{port} で起動します（停止は Ctrl+C）")
    if not settings.access_key:
        console.print("[dim]アクセスキー未設定のため認証なしで動作します（ローカル向け）[/]")
    if not settings.llm_available:
        console.print("[dim]ANTHROPIC_API_KEY 未設定のため、決定論的な検査のみ利用できます[/]")

    uvicorn.run("ir_review.api:create_app", host=host, port=port, reload=reload, factory=True)


if __name__ == "__main__":
    app()
