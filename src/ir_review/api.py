"""HTTP API と Web UI。

公開する前提なので、入口で止めるべきものを最初から入れている。

  - アクセスキー（設定されているときのみ有効。ローカルでは無効化できる）
  - レート制限（キーが共有されても回数で頭打ちにする）
  - アップロードサイズと拡張子の制限
  - ページ数・セグメント数の上限（LLM呼び出し回数の上限そのもの）
  - 同時実行数の上限
  - アップロードファイルは処理後に必ず削除する（保存しない）

長い処理は受付と実行を分け、クライアントに進捗を問い合わせてもらう（jobs.py 参照）。
"""

from __future__ import annotations

import secrets
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from .config import Settings, load_env_file
from .extract import load_document
from .glossary import Glossary
from .jobs import Job, JobStore
from .models import ReviewResult
from .pipeline import run_review
from .ratelimit import RateLimiter
from .web import INDEX_HTML

ALLOWED_SUFFIXES = {".pdf", ".txt", ".md", ".markdown"}
MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 8MB。開示資料のPDFとしては十分で、事故は止まる大きさ
DEFAULT_GLOSSARY = Path("data/glossary.sample.yaml")


class AppState:
    """アプリが持つ可変の状態。テストから差し替えられるようにまとめている。"""

    def __init__(self, settings: Settings, glossary: Glossary) -> None:
        self.settings = settings
        self.glossary = glossary
        self.jobs = JobStore(max_concurrent=1)
        self.limiter = RateLimiter(settings.rate_limit_per_hour)


def _client_key(request: Request) -> str:
    """レート制限のキー。

    リバースプロキシ配下では X-Forwarded-For の先頭が実クライアント。
    偽装できる値なので課金や認証の根拠にはせず、あくまで粗い抑制に使う。
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def require_access_key(request: Request) -> None:
    """アクセスキーの検証。

    設定が空のときは認証なし（ローカル実行を想定）。
    比較に compare_digest を使うのは、応答時間の差からキーを推測されないため。
    """
    expected = request.app.state.app.settings.access_key
    if not expected:
        return
    provided = request.headers.get("x-access-key") or ""
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="アクセスキーが正しくありません")


def enforce_rate_limit(request: Request) -> None:
    state: AppState = request.app.state.app
    key = _client_key(request)
    allowed, _remaining = state.limiter.check(key)
    if not allowed:
        wait = state.limiter.retry_after_seconds(key)
        raise HTTPException(
            status_code=429,
            detail=f"リクエストが多すぎます。{wait} 秒後に再度お試しください。",
            headers={"Retry-After": str(wait)},
        )


def _save_upload(upload: UploadFile) -> Path:
    """アップロードを一時ファイルへ書き出す。上限を超えたら途中で止める。

    全部読んでからサイズを見るとメモリを取られるので、書きながら数える。
    """
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"対応していない形式です（{', '.join(sorted(ALLOWED_SUFFIXES))} のみ）",
        )

    written = 0
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        path = Path(tmp.name)
        while chunk := upload.file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                tmp.close()
                path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"ファイルが大きすぎます（上限 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB）",
                )
            tmp.write(chunk)
    if written == 0:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="ファイルが空です")
    return path


def _build_work(state: AppState, path: Path, document_name: str, translate: bool) -> Any:
    """ジョブ本体を組み立てる。

    アップロードファイルは必ず削除する。預かったものを残さないため。
    """

    def work(job: Job) -> ReviewResult:
        try:
            segments = load_document(path, max_pages=state.settings.max_pages)
            if not segments:
                raise ValueError("本文を抽出できませんでした（画像PDFの可能性があります）")

            job.total = min(len(segments), state.settings.max_segments)

            translator = None
            reviewer = None
            if translate:
                from .llm import ClaudeReviewer, ClaudeTranslator

                translator = ClaudeTranslator(state.settings)
                reviewer = ClaudeReviewer(state.settings)

            def on_progress(done: int, total: int, _segment: Any) -> None:
                job.done = done
                job.total = total
                if translator is not None:
                    job.input_tokens = translator.input_tokens + (
                        reviewer.input_tokens if reviewer else 0
                    )
                    job.output_tokens = translator.output_tokens + (
                        reviewer.output_tokens if reviewer else 0
                    )

            result = run_review(
                document_name,
                segments,
                state.glossary,
                settings=state.settings,
                translator=translator,
                reviewer=reviewer,
                on_progress=on_progress,
            )
            if not translate:
                job.done = job.total
            return result
        finally:
            path.unlink(missing_ok=True)

    return work


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)


@router.get("/healthz")
def healthz(request: Request) -> dict[str, object]:
    state: AppState = request.app.state.app
    return {
        "status": "ok",
        "llm_available": state.settings.llm_available,
        "auth_required": bool(state.settings.access_key),
        "max_pages": state.settings.max_pages,
        "max_segments": state.settings.max_segments,
    }


@router.post(
    "/api/review",
    dependencies=[Depends(require_access_key), Depends(enforce_rate_limit)],
)
def create_review(
    request: Request,
    file: Annotated[UploadFile, File()],
    translate: Annotated[bool, Form()] = False,
) -> JSONResponse:
    state: AppState = request.app.state.app

    if translate and not state.settings.llm_available:
        raise HTTPException(
            status_code=503, detail="このサーバーでは翻訳機能が有効になっていません"
        )

    path = _save_upload(file)
    name = Path(file.filename or "document").name

    job = state.jobs.submit(name, _build_work(state, path, name, translate))
    if job is None:
        path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=429, detail="別のレビューを処理中です。完了後にお試しください。"
        )
    return JSONResponse({"job_id": job.id}, status_code=202)


@router.get("/api/jobs/{job_id}", dependencies=[Depends(require_access_key)])
def get_job(request: Request, job_id: str) -> dict[str, object]:
    state: AppState = request.app.state.app
    job = state.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    return job.snapshot()


def create_app(settings: Settings | None = None, glossary: Glossary | None = None) -> FastAPI:
    load_env_file()
    resolved = settings or Settings.from_env()
    resolved_glossary = glossary or Glossary.from_yaml(DEFAULT_GLOSSARY)

    app = FastAPI(title="IR開示資料 レビュー・翻訳エージェント", docs_url="/docs")
    app.state.app = AppState(resolved, resolved_glossary)
    app.include_router(router)
    return app
