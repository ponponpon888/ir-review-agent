# 依存の解決とアプリの実行を分け、実行イメージにビルド用の道具を残さない
FROM python:3.12-slim AS builder

WORKDIR /build
ENV PIP_NO_CACHE_DIR=1

COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install ".[llm,api]"

FROM python:3.12-slim

# root で動かさない。コンテナが乗っ取られたときの被害を狭めるため
RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app data ./data

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

USER app
EXPOSE 8000

# PORT は Render 等が実行時に指定するため、シェル形式で展開する
CMD uvicorn ir_review.api:create_app --factory --host 0.0.0.0 --port ${PORT}
