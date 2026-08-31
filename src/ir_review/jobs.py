"""ジョブ管理。

1文書のレビューは数十秒〜数分かかる。HTTPリクエストの中で最後まで待つと、
ブラウザやリバースプロキシのタイムアウトに引っかかり、
「処理は走っているのに結果が受け取れない」という一番たちの悪い失敗になる。

そこで受付と実行を分け、クライアントには進捗を問い合わせてもらう。

インメモリで持っているのは、この用途に対して十分だから。
デモと単一プロセス運用が前提で、再起動でジョブが消えることは許容する
（消えたら再実行すればよく、失われて困る状態を持たない）。
複数インスタンスに増やすときは、この層をRedis等に差し替える。
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from .models import ReviewResult


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


@dataclass
class Job:
    id: str
    document_name: str
    status: JobStatus = JobStatus.QUEUED
    done: int = 0
    total: int = 0
    result: ReviewResult | None = None
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def snapshot(self) -> dict[str, object]:
        """APIが返す形。結果は完了時のみ含める。"""
        payload: dict[str, object] = {
            "job_id": self.id,
            "document_name": self.document_name,
            "status": self.status.value,
            "done": self.done,
            "total": self.total,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }
        if self.status is JobStatus.DONE and self.result is not None:
            payload["result"] = self.result.model_dump(mode="json")
            payload["summary"] = {
                "by_severity": self.result.counts_by_severity(),
                "by_source": self.result.counts_by_source(),
            }
        if self.status is JobStatus.ERROR:
            payload["error"] = self.error
        return payload


class JobStore:
    """ジョブの保管と実行。

    同時実行数に上限を置いているのは、公開デモで並列に走らせると
    APIコストと実行時間が読めなくなるため。受け付けた時点で拒否して、
    利用者に「今は混んでいる」と分かる形で返す。
    """

    def __init__(self, *, max_concurrent: int = 1, ttl_seconds: float = 3600) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._max_concurrent = max_concurrent
        self._ttl = ttl_seconds

    def _purge_expired(self) -> None:
        """古い結果を捨てる。呼び出し側は _lock を保持していること。"""
        now = time.time()
        for job_id, job in list(self._jobs.items()):
            if job.finished_at is not None and now - job.finished_at > self._ttl:
                del self._jobs[job_id]

    def active_count(self) -> int:
        with self._lock:
            return sum(
                1 for j in self._jobs.values() if j.status in (JobStatus.QUEUED, JobStatus.RUNNING)
            )

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def submit(
        self,
        document_name: str,
        work: Callable[[Job], ReviewResult],
    ) -> Job | None:
        """ジョブを登録して実行を開始する。上限に達していれば None を返す。

        work は Job を受け取り、進捗を job.done / job.total に書きながら結果を返す。
        """
        with self._lock:
            self._purge_expired()
            active = sum(
                1 for j in self._jobs.values() if j.status in (JobStatus.QUEUED, JobStatus.RUNNING)
            )
            if active >= self._max_concurrent:
                return None
            job = Job(id=uuid.uuid4().hex, document_name=document_name)
            self._jobs[job.id] = job

        def _run() -> None:
            job.status = JobStatus.RUNNING
            try:
                job.result = work(job)
                job.status = JobStatus.DONE
            except Exception as exc:  # noqa: BLE001 - 実行中の例外はジョブの状態として返す
                job.error = f"{type(exc).__name__}: {exc}"
                job.status = JobStatus.ERROR
            finally:
                job.finished_at = time.time()

        threading.Thread(target=_run, daemon=True, name=f"review-{job.id[:8]}").start()
        return job
