"""Escaped HTML and spreadsheet-safe reports generated from persisted results."""
import csv
import io
import json
from html import escape
from . import NAME, VERSION
from .checking import STATUS_LABELS
from .importing import formula_safe


def display(value):
    if value is None:
        return "未提供"
    if type(value) is bool:
        return "是" if value else "否"
    return str(value)


def table_csv(headers, rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow([formula_safe(display(value)) for value in row])
    return "\ufeff" + output.getvalue()


def findings_csv(job):
    return table_csv(
        ["资产编号", "资产名称", "配置版本", "规则编号", "规则名称", "状态",
         "严重程度", "实际值", "预期值", "原因", "建议"],
        [[item["asset_key"], item["asset_name"], item["asset_revision"],
          item["rule_id"], item["rule_name"], STATUS_LABELS[item["status"]],
          item["severity"], item["observed"], item["expected"],
          item["reason"], item["guidance"]] for item in job["result"]["findings"]],
    )


def tickets_csv(rows):
    return table_csv(
        ["工单编号", "资产编号", "规则", "标题", "负责人", "截止日期", "状态", "说明"],
        [[row["id"], row["asset_key"], row["rule_id"], row["title"],
          row["owner"], row["due_date"], row["status"], row["note"]] for row in rows],
    )


def audit_csv(rows):
    return table_csv(
        ["序号", "时间UTC", "操作者", "动作", "对象", "明细", "前序摘要", "摘要"],
        [[row["seq"], row["timestamp"], row["actor"], row["action"],
          row["entity"], row["detail_json"], row["previous_hash"], row["entry_hash"]]
         for row in rows],
    )


def report_json(job):
    return json.dumps(job, ensure_ascii=False, allow_nan=False, indent=2)


def report_html(job):
    snapshot = job["snapshot"]
    result = job["result"]
    summary = result["summary"]
    esc = lambda value: escape(display(value), quote=True)
    rows = "".join(
        "<tr>" + "".join("<td>" + esc(value) + "</td>" for value in [
            item["asset_key"], item["rule_id"], item["rule_name"],
            STATUS_LABELS[item["status"]], item["observed"], item["expected"],
            item["reason"],
        ]) + "</tr>" for item in result["findings"]
    )
    counters = " / ".join(
        f"{STATUS_LABELS[key]} {value}" for key, value in summary["counts"].items()
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{esc(job['name'])} - 核查报告</title>
<style>
body {{ font-family: sans-serif; margin: 36px auto; max-width: 1100px; color: #18304a; }}
h1 {{ font-size: 26px; }}
h2 {{ font-size: 18px; border-bottom: 1px solid #cbd5e1; padding-bottom: 10px; }}
p {{ line-height: 1.8; }}
table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
td,th {{ border: 1px solid #cbd5e1; padding: 9px; text-align: left; }}
th {{ background: #e8eef5; }}
.hash {{ font-family: monospace; overflow-wrap: anywhere; }}
.notice {{ border-left: 4px solid #63798e; padding: 12px; background: #f3f6f9; }}
@media print {{ body {{ margin: 12mm; }} tr {{ break-inside: avoid; }} }}
</style></head><body>
<h1>{esc(NAME)} V{esc(VERSION)}</h1>
<h2>{esc(job['name'])}</h2>
<p>管理域：{esc(snapshot['space_name'])}；配置集合版本：{esc(snapshot['space_revision'])}<br>
基线：{esc(snapshot['baseline']['name'])}；任务：{esc(job['id'])}<br>
完成时间：{esc(job['finished_at'])}（UTC）；计算耗时：{esc(result['elapsed_ms'])} ms</p>
<p>{esc(counters)}</p>
<p>证据完整率：{esc(summary['coverage_percent'])}%<br>
已知项通过率：{esc(summary['pass_percent_known'])}%<br>
证据评分：{esc(summary['evidence_score'])}（未知项计入分母，不计入通过分值）</p>
<p class="notice">本报告仅核对用户填报的配置与自定义规则。不连接目标主机，
不验证配置是否真实实施，不等同于测评报告、保密认证或安全合规结论。
不适用项与未启用项不参与评分；待补证不是通过。</p>
<h2>输入与结果摘要</h2>
<p class="hash">快照 SHA-256：{esc(job['snapshot_hash'])}<br>
规则 SHA-256：{esc(snapshot['baseline']['content_hash'])}<br>
结果 SHA-256：{esc(job['result_hash'])}</p>
<h2>逐项核查结果</h2>
<table><thead><tr><th>资产</th><th>规则</th><th>说明</th><th>状态</th>
<th>实际值</th><th>阈值</th><th>判断理由</th></tr></thead><tbody>{rows}</tbody></table>
</body></html>"""
