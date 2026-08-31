"""レート制限。

公開デモに置く以上、1人が連続で叩けばAPIコストがそのまま増える。
アクセスキーは共有されうる（面談で伝える、画面に映る）ので、
キーによる認証とは別に、呼び出し回数そのものにも上限を掛ける。

インメモリのスライディングウィンドウ。単一プロセス前提で、
複数インスタンスに増やすときは共有ストアに差し替える。
"""

from __future__ import annotations

import threading
import time
from collections import deque


class RateLimiter:
    """クライアントごとに「直近 window 秒間で limit 回まで」を許可する。"""

    def __init__(self, limit: int, window_seconds: float = 3600) -> None:
        self._limit = limit
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] > self._window:
            hits.popleft()
        return hits

    def check(self, key: str) -> tuple[bool, int]:
        """1回分を記録し、(許可されたか, 残り回数) を返す。

        limit が 0 以下なら無制限として扱う（ローカル実行を想定）。
        """
        if self._limit <= 0:
            return True, -1
        now = time.time()
        with self._lock:
            hits = self._prune(key, now)
            if len(hits) >= self._limit:
                return False, 0
            hits.append(now)
            return True, self._limit - len(hits)

    def retry_after_seconds(self, key: str) -> int:
        """次に1回空くまでの秒数。制限に掛かった相手に待ち時間を返すために使う。"""
        if self._limit <= 0:
            return 0
        now = time.time()
        with self._lock:
            hits = self._prune(key, now)
            if len(hits) < self._limit:
                return 0
            return max(1, int(self._window - (now - hits[0])) + 1)
