"""Real HTTP browser acceptance flow; no route mocks or rendered prototypes."""
from pathlib import Path
from datetime import date, timedelta
import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def available_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=".evidence/browser")
    parser.add_argument("--chromium", default=os.environ.get("CHROMIUM_PATH"))
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    screenshots = output / "screenshots"
    screenshots.mkdir(exist_ok=True)
    downloads = output / "downloads"
    downloads.mkdir(exist_ok=True)
    steps, errors = [], []
    password = secrets.token_urlsafe(24)
    port = available_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="deskguard-browser-") as directory:
        env = os.environ | {"DESKGUARD_HOME": directory, "DESKGUARD_ADMIN_PASSWORD": password}
        initialized = subprocess.run(
            [sys.executable, "-m", "deskguard", "init"], cwd=ROOT, env=env,
            text=True, capture_output=True, check=True,
        )
        (output / "init.log").write_text(initialized.stdout, encoding="utf-8")
        server_log = (output / "server.log").open("w", encoding="utf-8")
        server = subprocess.Popen(
            [sys.executable, "-m", "deskguard", "serve", "--port", str(port)],
            cwd=ROOT, env=env, stdout=server_log, stderr=subprocess.STDOUT,
        )
        try:
            for _ in range(100):
                if server.poll() is not None:
                    raise RuntimeError("HTTP server stopped before health check")
                try:
                    with urllib.request.urlopen(base + "/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except Exception:
                    time.sleep(.1)
            else:
                raise RuntimeError("HTTP health check timed out")
            with sync_playwright() as playwright:
                options = {"headless": True, "args": ["--no-sandbox"]}
                if args.chromium:
                    options["executable_path"] = args.chromium
                browser = playwright.chromium.launch(**options)
                context = browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    device_scale_factor=1, locale="zh-CN", timezone_id="Asia/Shanghai",
                    accept_downloads=True,
                )
                page = context.new_page()
                page.on("pageerror", lambda exc: errors.append(str(exc)))
                page.set_default_timeout(15000)

                def step(title, action, expected, target=None):
                    current = target or page
                    current.evaluate("document.fonts.ready")
                    current.wait_for_timeout(120)
                    path = screenshots / f"step-{len(steps)+1:02}.png"
                    current.screenshot(path=str(path), full_page=False, animations="disabled")
                    steps.append({
                        "step": len(steps) + 1, "title": title, "action": action,
                        "expected": expected, "screenshot": "screenshots/" + path.name,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    })
                    print(f"STEP {len(steps):02} PASS: {title}", flush=True)

                def nav(name, heading):
                    page.locator(f'[data-view="{name}"]').click()
                    expect(page.locator("#content h2").first).to_have_text(heading)

                def close():
                    page.locator("#dialog-close").click()
                    expect(page.locator("#editor")).not_to_be_visible()

                def download(selector, name):
                    with page.expect_download() as info:
                        page.locator(selector).click()
                    data = info.value
                    data.save_as(downloads / name)
                    assert (downloads / name).stat().st_size > 0

                def create_job(name):
                    page.locator("#new-job").click()
                    page.locator("#job-name").fill(name)
                    page.locator("#save-editor").click()
                    expect(page.locator("#editor")).not_to_be_visible()
                    row = page.locator("#content tr").filter(has_text=name)
                    expect(row).to_be_visible()
                    return row

                page.goto(base, wait_until="domcontentloaded")
                expect(page.locator("#login-form")).to_be_visible()
                step("打开登录页面", "在浏览器访问本机服务地址。", "显示软件全称、版本与登录表单。")
                page.locator("#username").fill("admin")
                step("输入管理员用户名", "在用户名框输入初始化时设置的用户名admin。", "用户名框显示admin。")
                page.locator("#password").fill(password)
                step("输入管理员口令", "填写本机初始化的口令，界面以掩码显示。", "口令不以明文显示。")
                page.locator("#login-button").click()
                expect(page.locator("#content h2")).to_have_text("工作概览")
                step("登录并核对工作概览", "点击登录。", "显示工作概览与当前管理员。")

                nav("spaces", "管理域")
                page.locator("#new-space").click()
                expect(page.locator("#dialog-title")).to_have_text("新建管理域")
                step("打开管理域创建表单", "选择管理域页面，点击新建管理域。", "出现管理域名称与说明输入框。")
                page.locator("#space-name").fill("办公桌面合成测试域")
                page.locator("#space-desc").fill("仅用于演示的合成资产配置，不连接真实设备。")
                step("填写管理域范围", "录入管理域名称及配置范围说明。", "名称与说明均显示在表单中。")
                page.locator("#save-editor").click()
                expect(page.locator("#editor")).not_to_be_visible()
                expect(page.locator("#content")).to_contain_text("办公桌面合成测试域")
                step("保存管理域", "点击创建管理域。", "列表显示启用状态、0条资产和初始版本。")

                nav("assets", "资产与配置")
                page.locator("#demo-assets").click()
                expect(page.locator("#content")).to_contain_text("HOST-01")
                expect(page.locator("#content tbody tr")).to_have_count(4)
                step("载入合成资产", "在资产与配置页面点击载入合成示例。", "新增4条明确标注为合成的资产，覆盖四种角色。")
                host_row = page.locator("#content tr").filter(has_text="HOST-01")
                host_row.locator("button").click()
                expect(page.locator("#dialog-title")).to_have_text("资产配置详情")
                step("查看配置证据", "打开HOST-01的配置详情。", "显示角色、负责人、版本及12个配置字段。")
                page.locator("#asset-history").click()
                expect(page.locator("#dialog-title")).to_have_text("配置版本记录")
                step("核对初始配置版本", "点击查看配置版本。", "历史记录含版本1及内容摘要核验结果。")
                close()
                page.locator("#import-assets").click()
                expect(page.locator("#dialog-title")).to_have_text("导入资产配置")
                download("#template-json", "assets-template.json")
                step("下载资产导入模板", "点击导入，再点击下载JSON模板。", "浏览器实际下载JSON模板；当前界面保留导入表单。")
                demo = json.loads((downloads / "assets-template.json").read_text())
                imported = dict(demo[2], asset_key="VDI-02", name="合成研发桌面02")
                imported["config"] = imported["config"] | {"usb_blocked": True}
                page.locator("#import-content").fill(json.dumps([imported], ensure_ascii=False, indent=2))
                step("填入新资产JSON", "按模板录入VDI-02，不使用已有资产编号。", "导入格式为JSON，内容为单条合成资产数组。")
                page.locator("#save-editor").click()
                expect(page.locator("#editor")).not_to_be_visible()
                expect(page.locator("#content tbody tr")).to_have_count(5)
                step("校验并导入资产", "点击校验并导入。", "资产总数变为5，VDI-02出现在列表中。")
                download("#assets-csv", "assets.csv")
                step("导出资产CSV", "点击导出CSV。", "浏览器实际下载包含当前5条资产的CSV文件。")

                nav("baselines", "规则基线")
                page.locator("#new-baseline").click()
                expect(page.locator("#dialog-title")).to_have_text("创建规则基线")
                step("打开基线配置", "进入规则基线，点击创建基线。", "表单列出12条规则、适用角色与阈值。")
                page.locator("#baseline-name").fill("办公配置核查基线V1")
                step("设置基线名称与规则", "填写基线名称，保留本例12条规则的启用状态及默认阈值。", "基线内容为用户自定示例，不代表法定标准。")
                page.locator("#save-editor").click()
                expect(page.locator("#editor")).not_to_be_visible()
                expect(page.locator("#content")).to_contain_text("办公配置核查基线V1")
                step("保存不可变规则基线", "点击保存基线。", "列表显示12条已启用规则及内容摘要。")
                page.locator('[data-baseline]').first.click()
                expect(page.locator("#dialog-title")).to_have_text("规则基线详情")
                step("查看基线摘要", "打开基线详情。", "显示SHA-256摘要、检查字段和规则内容。")
                close()

                nav("jobs", "核查任务")
                page.locator("#new-job").click()
                page.locator("#job-name").fill("办公配置首次核查")
                step("配置首次核查任务", "填写任务名称，并选择已有管理域与基线。", "表单显示固定输入快照的说明。")
                page.locator("#save-editor").click()
                expect(page.locator("#editor")).not_to_be_visible()
                step("创建任务输入快照", "点击创建快照任务。", "任务列表显示待执行状态及快照摘要。")
                first_row = page.locator("#content tr").filter(has_text="办公配置首次核查")
                first_job_id = first_row.locator('[data-job]').get_attribute("data-job")
                first_row.locator('[data-run]').click()
                expect(page.locator("#dialog-title")).to_have_text("办公配置首次核查")
                expect(page.locator("#job-tab-body")).to_contain_text("已完成")
                step("执行配置核查", "点击任务的执行按钮。", "实际规则引擎完成核查，显示问题数、待补证数和证据评分。")
                page.locator('[data-tab="findings"]').click()
                page.locator("#finding-filter").select_option("fail")
                expect(page.locator("#finding-table tbody tr")).to_have_count(4)
                step("筛选不通过项", "切换逐项结果，将结果筛选设为不通过。", "显示4条不通过项及实际值、预期值和工单入口。")
                page.locator("#finding-filter").select_option("unknown")
                expect(page.locator("#finding-table tbody tr")).to_have_count(1)
                step("查看待补证项", "将结果筛选设为待补证。", "终端防护的缺失配置单独列示，不标记为通过。")
                page.locator('[data-tab="evidence"]').click()
                expect(page.locator("#job-tab-body")).to_contain_text("SHA-256")
                step("核对输入与结果证据", "切换输入与证据页签。", "显示输入、规则和结果摘要以及资产配置版本。")
                page.locator('[data-tab="summary"]').click()
                with context.expect_page() as popup_info:
                    page.locator("#report-html").click()
                report = popup_info.value
                report.wait_for_load_state("domcontentloaded")
                expect(report.locator("body")).to_contain_text("逐项核查结果")
                step("预览核查报告", "点击预览报告，在新页面查看生成的HTML报告。", "报告展示真实持久化结果、输入摘要及核查边界。", report)
                report.close()
                download("#report-json", "first-result.json")
                download("#report-csv", "first-result.csv")
                step("导出首次核查结果", "点击导出JSON及导出CSV。", "浏览器实际下载结构化完整结果与逐项核查表。")
                first_result = json.loads((downloads / "first-result.json").read_text())
                assert first_result["result"]["summary"]["counts"]["fail"] == 4
                assert first_result["result"]["summary"]["counts"]["unknown"] == 1
                page.locator('[data-tab="findings"]').click()
                page.locator("#finding-filter").select_option("fail")
                target = page.locator("#finding-table tr").filter(has_text="HOST-01").filter(has_text="DG-01")
                target.locator("button").click()
                expect(page.locator("#dialog-title")).to_have_text("建立整改工单")
                page.locator("#ticket-owner").fill("演示运维组")
                page.locator("#ticket-due").fill((date.today() + timedelta(days=7)).isoformat())
                page.locator("#ticket-note").fill("合成主机未启用管理多因素认证，需要更新配置证据。")
                step("登记整改责任与期限", "从HOST-01的DG-01问题建立工单，填写负责人、日期及说明。", "表单绑定原始不通过项，而不是任意新建问题。")
                page.locator("#save-editor").click()
                expect(page.locator("#editor")).not_to_be_visible()
                expect(page.locator("#content h2")).to_have_text("整改工单")
                step("保存整改工单", "点击创建工单。", "列表显示待处理状态与计划完成日期。")
                ticket_id = page.locator('[data-ticket]').first.get_attribute("data-ticket")
                page.locator('[data-ticket]').first.click()
                page.locator("#action-note").fill("已接收工单，开始核对合成资产的配置记录。")
                page.locator('[data-transition="in_progress"]').click()
                expect(page.locator('[data-transition="resolved"]')).to_be_visible()
                step("开始处理问题", "填写处理说明，点击开始处理。", "工单转为处理中，轨迹保留处理记录。")
                close()

                nav("assets", "资产与配置")
                page.locator("#content tr").filter(has_text="HOST-01").locator("button").click()
                page.locator("#asset-edit").click()
                expect(page.locator("#dialog-title")).to_have_text("更新配置证据")
                step("打开资产配置更新", "查看HOST-01并点击更新配置。", "资产编号和角色锁定，允许更新配置值并产生新版本。")
                page.locator("#cfg-mfa").select_option("true")
                step("补录修复后的配置", "将管理多因素认证改为是；本例只是修改合成配置记录。", "新值显示为是，尚未保存。")
                page.locator("#save-editor").click()
                expect(page.locator("#editor")).not_to_be_visible()
                row = page.locator("#content tr").filter(has_text="HOST-01")
                expect(row.locator("td").nth(4)).to_have_text("2")
                step("保存新配置版本", "点击保存新版本。", "HOST-01配置版本升为2；其他资产与旧核查任务保持不变。")
                row.locator("button").click()
                page.locator("#asset-history").click()
                expect(page.locator("#dialog-body tbody tr")).to_have_count(2)
                step("核查新旧配置留存", "打开配置版本记录。", "版本1和版本2同时存在，摘要校验均通过。")
                close()

                nav("tickets", "整改工单")
                page.locator(f'[data-ticket="{ticket_id}"]').click()
                page.locator("#action-note").fill("已在配置登记中保存新版本，提交同规则复测验证。")
                page.locator('[data-transition="resolved"]').click()
                expect(page.locator("#verify-ticket")).to_be_visible()
                step("提交工单待验证", "填写更新情况并点击提交待验证。", "工单进入待验证，不能直接人工标记关闭。")
                # Deliberate negative check: selecting the old job must be rejected.
                page.locator("#verify-job").select_option(first_job_id)
                page.locator("#action-note").fill("验证旧任务不能作为修复通过的依据。")
                page.locator("#verify-ticket").click()
                expect(page.locator("#dialog-error")).to_be_visible()
                page.locator("#dialog-error").scroll_into_view_if_needed()
                expect(page.locator("#dialog-error")).to_be_in_viewport()
                step("确认旧证据不能关闭工单", "选择原始任务并尝试验证关闭。", "系统明确拒绝；工单没有关闭。")
                close()

                nav("jobs", "核查任务")
                row = create_job("办公配置整改复测")
                second_job_id = row.locator('[data-job]').get_attribute("data-job")
                step("创建同基线复测任务", "使用原管理域与原基线，新建办公配置整改复测任务。", "新任务固定当前配置版本，原始任务仍保留。")
                row.locator('[data-run]').click()
                expect(page.locator("#dialog-title")).to_have_text("办公配置整改复测")
                expect(page.locator("#job-tab-body")).to_contain_text("已完成")
                step("执行更新配置的复测", "执行新建复测任务。", "核查再次真实运行，不通过项由4降为3，待补证项仍为1。")
                download("#report-json", "retest-result.json")
                second_result = json.loads((downloads / "retest-result.json").read_text())
                assert second_result["result"]["summary"]["counts"]["fail"] == 3
                close()

                nav("compare", "复测对比")
                page.locator("#compare-before").select_option(first_job_id)
                page.locator("#compare-after").select_option(second_job_id)
                step("选择原始与复测任务", "在复测对比中分别选择首次核查和整改复测。", "同管理域、同规则的两个任务已选定。")
                page.locator("#compare-submit").click()
                expect(page.locator("#compare-result")).to_contain_text("HOST-01")
                expect(page.locator("#compare-result tbody tr")).to_have_count(1)
                step("查看整改前后变化", "点击对比结果。", "仅DG-01从不通过变为通过，已修复数为1。")

                nav("tickets", "整改工单")
                page.locator(f'[data-ticket="{ticket_id}"]').click()
                page.locator("#verify-job").select_option(second_job_id)
                page.locator("#action-note").fill("更新后的配置版本2在相同基线下复测通过DG-01。")
                step("绑定新的复测证据", "选择整改复测任务并填写验证说明。", "验证使用新配置版本，不使用原始失败任务。")
                page.locator("#verify-ticket").click()
                expect(page.locator("#dialog-body")).to_contain_text("已关闭")
                expect(page.locator("#verify-ticket")).to_have_count(0)
                step("验证通过并关闭工单", "点击验证并关闭。", "工单实际关闭，验证任务ID与处理轨迹保留。")
                close()
                download("#tickets-export", "tickets.csv")
                step("导出整改工单", "点击导出工单。", "CSV包含关闭状态、责任人与处理说明。")

                nav("audit", "操作审计")
                page.locator("#audit-filter").select_option("ticket.")
                expect(page.locator("#audit-table")).to_contain_text("ticket.verified")
                step("筛选整改审计记录", "在操作审计中将动作筛选设为整改工单。", "列表显示创建、处理、提交和验证关闭事件。")
                page.locator("#audit-verify").click()
                expect(page.locator("#audit-verification")).to_contain_text("本地审计链一致")
                step("校验本地审计链", "点击校验本地审计链。", "显示实际记录条数和尾部摘要；不宣称外部防篡改存证。")
                download("#audit-export", "audit.csv")
                step("导出操作审计", "点击导出审计。", "浏览器下载最近最多1000条审计记录。")

                nav("system", "系统维护")
                expect(page.locator("#content")).to_contain_text("deskguard-rules-v1")
                step("查看本机运行信息", "进入系统维护页面。", "显示引擎版本、数据库名称、规则数和资产上限。")
                page.locator("#create-backup").click()
                expect(page.locator("#backup-result")).to_contain_text("SHA-256")
                assert list((Path(directory) / "backups").glob("*.sqlite3"))
                step("创建数据库一致性备份", "点击创建本地备份。", "真实SQLite备份已生成，页面显示文件名、大小及摘要。")
                page.locator("#change-password").click()
                expect(page.locator("#dialog-title")).to_have_text("修改管理员口令")
                step("查看口令维护入口", "点击修改管理员口令；本步骤仅查看，不提交新口令。", "显示旧口令、新口令及会话撤销说明。")
                close()
                page.locator("#logout").click()
                expect(page.locator("#login-screen")).to_be_visible()
                step("退出系统", "点击右上角退出。", "会话撤销，返回登录页面。")
                assert not errors, errors
                summary = {
                    "status": "PASS", "steps": len(steps), "page_errors": errors,
                    "browser": browser.version, "transport": "real loopback HTTP",
                    "route_mocks": False, "synthetic_assets": True,
                    "first_job_id": first_job_id, "retest_job_id": second_job_id,
                    "ticket_id": ticket_id, "first_counts": first_result["result"]["summary"]["counts"],
                    "retest_counts": second_result["result"]["summary"]["counts"],
                    "downloads": [p.name for p in sorted(downloads.iterdir())],
                }
                (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
                print(json.dumps(summary, ensure_ascii=False), flush=True)
                browser.close()
        finally:
            (output / "steps.json").write_text(json.dumps(steps, ensure_ascii=False, indent=2))
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
            server_log.close()


if __name__ == "__main__":
    main()
