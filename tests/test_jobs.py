"""ジョブ管理のテスト。"""

from __future__ import annotations

import time

from ir_review.jobs import Job, JobStatus, JobStore
from ir_review.models import ReviewResult, Segment


def _result(name: str = "doc") -> ReviewResult:
    return ReviewResult(
        document_name=name,
        segments=[Segment(id="p1-s000", page=1, order=0, text="本文です。")],
    )


def _wait(store: JobStore, job_id: str, timeout: float = 2.0) -> Job:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = store.get(job_id)
        assert job is not None
        if job.status in {JobStatus.DONE, JobStatus.ERROR}:
            return job
        time.sleep(0.01)
    raise AssertionError("ジョブが完了しませんでした")


def test_successful_job_holds_its_result() -> None:
    store = JobStore()
    job = store.submit("doc", lambda j: _result())
    assert job is not None

    finished = _wait(store, job.id)
    assert finished.status is JobStatus.DONE
    assert finished.result is not None
    assert "result" in finished.snapshot()


def test_failing_job_records_the_error() -> None:
    """例外でスレッドが黙って死なず、状態として残ること。"""

    def boom(job: Job) -> ReviewResult:
        raise RuntimeError("失敗しました")

    store = JobStore()
    job = store.submit("doc", boom)
    assert job is not None

    finished = _wait(store, job.id)
    assert finished.status is JobStatus.ERROR
    assert "失敗しました" in (finished.error or "")
    assert "result" not in finished.snapshot()


def test_concurrency_limit_rejects_extra_jobs() -> None:
    def slow(job: Job) -> ReviewResult:
        time.sleep(0.3)
        return _result()

    store = JobStore(max_concurrent=1)
    assert store.submit("a", slow) is not None
    assert store.submit("b", slow) is None  # 上限に達したら受け付けない


def test_slot_frees_up_after_completion() -> None:
    store = JobStore(max_concurrent=1)
    first = store.submit("a", lambda j: _result())
    assert first is not None
    _wait(store, first.id)

    assert store.submit("b", lambda j: _result()) is not None


def test_progress_is_visible_while_running() -> None:
    def work(job: Job) -> ReviewResult:
        job.total = 3
        for i in range(1, 4):
            job.done = i
            time.sleep(0.02)
        return _result()

    store = JobStore()
    job = store.submit("doc", work)
    assert job is not None
    finished = _wait(store, job.id)
    assert (finished.done, finished.total) == (3, 3)


def test_expired_jobs_are_purged() -> None:
    """結果を無期限に持つとメモリが増え続けるため。"""
    store = JobStore(ttl_seconds=0.05)
    first = store.submit("a", lambda j: _result())
    assert first is not None
    _wait(store, first.id)

    time.sleep(0.08)
    store.submit("b", lambda j: _result())  # 登録時に期限切れを掃除する
    assert store.get(first.id) is None


def test_snapshot_excludes_result_until_done() -> None:
    job = Job(id="x", document_name="doc")
    snap = job.snapshot()
    assert snap["status"] == "queued"
    assert "result" not in snap
