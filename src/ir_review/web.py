"""Web UI。

外部CDNに依存しない単一ページ。閉じたネットワークでも動き、
デモの最中に外部の障害で画面が崩れることがない。
"""

from __future__ import annotations

INDEX_HTML = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IR開示資料 レビュー・翻訳エージェント</title>
<style>
  :root {
    --bg: #f6f7f9; --panel: #ffffff; --ink: #16181d; --muted: #6b7280;
    --line: #e2e5ea; --accent: #1f3864;
    --high: #b42318; --high-bg: #fef3f2;
    --med: #b54708; --med-bg: #fffaeb;
    --low: #475467; --low-bg: #f2f4f7;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--ink);
    font-family: system-ui, -apple-system, "Segoe UI", "Hiragino Sans", "Noto Sans JP", sans-serif;
    line-height: 1.6;
  }
  .wrap { max-width: 980px; margin: 0 auto; padding: 32px 20px 64px; }
  header h1 { font-size: 20px; margin: 0 0 4px; }
  header p { margin: 0 0 24px; color: var(--muted); font-size: 14px; }
  .panel {
    background: var(--panel); border: 1px solid var(--line);
    border-radius: 10px; padding: 20px; margin-bottom: 20px;
  }
  label { display: block; font-size: 13px; font-weight: 600; margin-bottom: 6px; }
  input[type=file], input[type=password] {
    width: 100%; padding: 9px 10px; border: 1px solid var(--line);
    border-radius: 6px; font-size: 14px; background: #fff; font-family: inherit;
  }
  .row { margin-bottom: 16px; }
  .check { display: flex; align-items: flex-start; gap: 8px; }
  .check input { margin-top: 4px; }
  .check span { font-size: 13px; font-weight: 400; }
  .hint { color: var(--muted); font-size: 12px; margin-top: 4px; }
  button {
    background: var(--accent); color: #fff; border: 0; border-radius: 6px;
    padding: 10px 20px; font-size: 14px; font-weight: 600; cursor: pointer;
    font-family: inherit;
  }
  button:disabled { opacity: .5; cursor: not-allowed; }
  .bar { height: 6px; background: var(--low-bg); border-radius: 3px; overflow: hidden; }
  .bar > i { display: block; height: 100%; background: var(--accent); width: 0; transition: width .3s; }
  .status { font-size: 13px; color: var(--muted); margin-bottom: 8px; }
  .error { color: var(--high); background: var(--high-bg); border: 1px solid #fecdca;
           padding: 12px; border-radius: 6px; font-size: 14px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--line);
           vertical-align: top; }
  th { font-size: 12px; color: var(--muted); font-weight: 600; }
  .tag { display: inline-block; padding: 1px 8px; border-radius: 999px;
         font-size: 11px; font-weight: 700; white-space: nowrap; }
  .tag.high { color: var(--high); background: var(--high-bg); }
  .tag.medium { color: var(--med); background: var(--med-bg); }
  .tag.low { color: var(--low); background: var(--low-bg); }
  .src { font-size: 11px; color: var(--muted); white-space: nowrap; }
  code { background: var(--low-bg); padding: 1px 5px; border-radius: 4px; font-size: 12px; }
  .excerpt { color: var(--muted); font-size: 12px; margin-top: 4px;
             border-left: 2px solid var(--line); padding-left: 8px; }
  .summary { display: flex; gap: 20px; flex-wrap: wrap; font-size: 13px;
             color: var(--muted); margin-bottom: 14px; }
  .summary b { color: var(--ink); }
  details { margin-top: 10px; }
  summary { cursor: pointer; font-size: 13px; font-weight: 600; }
  .tr { border-bottom: 1px solid var(--line); padding: 10px 0; font-size: 13px; }
  .tr .ja { color: var(--muted); }
  .hidden { display: none; }
  .badge { font-size: 11px; color: var(--muted); border: 1px solid var(--line);
           border-radius: 4px; padding: 1px 6px; margin-left: 6px; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>IR開示資料 レビュー・翻訳エージェント</h1>
    <p>開示資料をアップロードすると、表記・用語・数値の検査と英訳レビューを行います。</p>
  </header>

  <form class="panel" id="form">
    <div class="row">
      <label for="file">ファイル（PDF / テキスト / Markdown・8MBまで）</label>
      <input type="file" id="file" accept=".pdf,.txt,.md,.markdown" required>
    </div>

    <div class="row" id="keyRow">
      <label for="key">アクセスキー</label>
      <input type="password" id="key" autocomplete="off" placeholder="発行されたキーを入力">
    </div>

    <div class="row">
      <label class="check">
        <input type="checkbox" id="translate">
        <span>英訳とAIレビューまで行う<br>
          <span class="hint">オフの場合は、LLMを使わない決定論的な検査のみを実行します（高速・無料）。</span>
        </span>
      </label>
    </div>

    <button type="submit" id="submit">レビューを実行</button>
  </form>

  <div class="panel hidden" id="progressPanel">
    <div class="status" id="status">準備中…</div>
    <div class="bar"><i id="barFill"></i></div>
  </div>

  <div class="panel hidden" id="errorPanel"><div class="error" id="errorText"></div></div>

  <div class="panel hidden" id="resultPanel">
    <div class="summary" id="summary"></div>
    <table>
      <thead><tr><th>重要度</th><th>出所</th><th>箇所</th><th>内容</th></tr></thead>
      <tbody id="findings"></tbody>
    </table>
    <div id="clean" class="hidden" style="padding:16px 0;font-size:14px;">指摘はありません。</div>
    <details id="transWrap" class="hidden">
      <summary>翻訳結果を表示</summary>
      <div id="translations"></div>
    </details>
  </div>
</div>

<script>
const $ = (id) => document.getElementById(id);
const LABEL = { high: "要修正", medium: "要確認", low: "軽微" };

let pollTimer = null;

function show(el, on) { el.classList.toggle("hidden", !on); }
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function init() {
  try {
    const r = await fetch("healthz");
    const h = await r.json();
    if (!h.auth_required) show($("keyRow"), false);
    if (!h.llm_available) {
      $("translate").disabled = true;
      $("translate").parentElement.querySelector(".hint").textContent =
        "このサーバーでは翻訳機能が有効になっていません。決定論的な検査のみ実行します。";
    }
  } catch (e) { /* healthz が取れなくてもフォームは使える */ }
}

function headers() {
  const key = $("key").value.trim();
  return key ? { "X-Access-Key": key } : {};
}

async function submit(ev) {
  ev.preventDefault();
  if (pollTimer) clearInterval(pollTimer);
  show($("errorPanel"), false);
  show($("resultPanel"), false);
  show($("progressPanel"), true);
  $("submit").disabled = true;
  $("status").textContent = "アップロード中…";
  $("barFill").style.width = "0%";

  const fd = new FormData();
  fd.append("file", $("file").files[0]);
  fd.append("translate", $("translate").checked ? "true" : "false");

  try {
    const res = await fetch("api/review", { method: "POST", body: fd, headers: headers() });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || `エラー (${res.status})`);
    poll(body.job_id);
  } catch (e) {
    fail(e.message);
  }
}

function poll(jobId) {
  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`api/jobs/${jobId}`, { headers: headers() });
      const job = await res.json();
      if (!res.ok) throw new Error(job.detail || `エラー (${res.status})`);

      if (job.total > 0) {
        $("barFill").style.width = `${Math.round((job.done / job.total) * 100)}%`;
      }
      const tok = job.input_tokens
        ? `　トークン 入力 ${job.input_tokens.toLocaleString()} / 出力 ${job.output_tokens.toLocaleString()}`
        : "";
      $("status").textContent =
        job.status === "running"
          ? `処理中 ${job.done}/${job.total}${tok}`
          : job.status === "queued" ? "順番待ち…" : "完了";

      if (job.status === "done") { clearInterval(pollTimer); render(job); }
      if (job.status === "error") { clearInterval(pollTimer); fail(job.error); }
    } catch (e) {
      clearInterval(pollTimer);
      fail(e.message);
    }
  }, 1000);
}

function fail(msg) {
  show($("progressPanel"), false);
  show($("errorPanel"), true);
  $("errorText").textContent = msg || "不明なエラーが発生しました";
  $("submit").disabled = false;
}

function render(job) {
  show($("progressPanel"), false);
  show($("resultPanel"), true);
  $("submit").disabled = false;

  const r = job.result, s = job.summary;
  $("summary").innerHTML = `
    <span>セグメント <b>${r.segments.length}</b></span>
    <span>要修正 <b>${s.by_severity.high}</b></span>
    <span>要確認 <b>${s.by_severity.medium}</b></span>
    <span>軽微 <b>${s.by_severity.low}</b></span>
    <span>ルール由来 <b>${s.by_source.rule}</b> / LLM由来 <b>${s.by_source.llm}</b></span>`;

  const order = { high: 0, medium: 1, low: 2 };
  const rows = [...r.findings].sort((a, b) =>
    order[a.severity] - order[b.severity] || a.segment_id.localeCompare(b.segment_id));

  show($("clean"), rows.length === 0);
  $("findings").innerHTML = rows.map((f) => `
    <tr>
      <td><span class="tag ${f.severity}">${LABEL[f.severity]}</span></td>
      <td class="src">${f.source === "rule" ? "ルール" : "LLM"}${
        f.rule_id ? `<span class="badge">${esc(f.rule_id)}</span>` : ""}</td>
      <td><code>${esc(f.segment_id)}</code></td>
      <td>${esc(f.message)}
        <div class="excerpt">${esc(f.excerpt)}</div>
        ${f.suggestion ? `<div class="hint">修正案: ${esc(f.suggestion)}</div>` : ""}</td>
    </tr>`).join("");

  const t = r.translations || [];
  show($("transWrap"), t.length > 0);
  $("translations").innerHTML = t.map((u) => `
    <div class="tr">
      <div><code>${esc(u.segment_id)}</code>${u.retried ? '<span class="badge">再翻訳</span>' : ""}</div>
      <div class="ja">${esc(u.source_text)}</div>
      <div>${esc(u.translated_text)}</div>
    </div>`).join("");
}

$("form").addEventListener("submit", submit);
init();
</script>
</body>
</html>
"""
