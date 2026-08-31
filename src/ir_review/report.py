"""レポート出力。

Markdown（人が読む）とJSON（機械が読む）の両方を出す。
指摘の並び順は severity の降順で固定し、同じ入力なら同じレポートになるようにしている。
"""

from __future__ import annotations

import json

from .models import Finding, FindingSource, ReviewResult, Severity

_SEVERITY_ORDER = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}
_SEVERITY_LABEL = {Severity.HIGH: "要修正", Severity.MEDIUM: "要確認", Severity.LOW: "軽微"}
_SOURCE_LABEL = {FindingSource.RULE: "ルール", FindingSource.LLM: "LLM"}


def sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda f: (_SEVERITY_ORDER[f.severity], f.segment_id, f.rule_id or "", f.message),
    )


def to_markdown(result: ReviewResult) -> str:
    counts = result.counts_by_severity()
    by_source = result.counts_by_source()
    lines: list[str] = [
        f"# レビュー結果: {result.document_name}",
        "",
        f"- セグメント数: {len(result.segments)}",
        f"- 指摘: 要修正 {counts['high']} / 要確認 {counts['medium']} / 軽微 {counts['low']}",
        f"- 内訳: ルール由来 {by_source['rule']} / LLM由来 {by_source['llm']}",
        "",
    ]

    if not result.findings:
        lines += ["指摘はありません。", ""]
    else:
        lines += ["## 指摘一覧", ""]
        lines += ["| 重要度 | 出所 | 規則 | 箇所 | 内容 |", "| --- | --- | --- | --- | --- |"]
        for f in sort_findings(result.findings):
            msg = f.message.replace("|", "\\|")
            lines.append(
                f"| {_SEVERITY_LABEL[f.severity]} | {_SOURCE_LABEL[f.source]} "
                f"| {f.rule_id or '-'} | {f.segment_id} | {msg} |"
            )
        lines.append("")

        lines += ["## 指摘の詳細", ""]
        for f in sort_findings(result.findings):
            lines += [
                f"### [{_SEVERITY_LABEL[f.severity]}] {f.segment_id}"
                f"（{_SOURCE_LABEL[f.source]}{' / ' + f.rule_id if f.rule_id else ''}）",
                "",
                f"{f.message}",
                "",
                f"> {f.excerpt}",
                "",
            ]
            if f.suggestion:
                lines += [f"修正案: {f.suggestion}", ""]

    if result.translations:
        lines += ["## 翻訳", ""]
        retried = sum(1 for t in result.translations if t.retried)
        if retried:
            lines += [f"※ 用語集違反により {retried} 件を再翻訳しました。", ""]
        for t in result.translations:
            lines += [
                f"**{t.segment_id}**",
                "",
                f"- 原文: {t.source_text}",
                f"- 訳文: {t.translated_text}",
                "",
            ]

    return "\n".join(lines)


def to_json(result: ReviewResult) -> str:
    payload = result.model_dump(mode="json")
    payload["findings"] = [f.model_dump(mode="json") for f in sort_findings(result.findings)]
    payload["summary"] = {
        "segments": len(result.segments),
        "by_severity": result.counts_by_severity(),
        "by_source": result.counts_by_source(),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
