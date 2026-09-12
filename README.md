# 安全虚拟桌面配置核查与整改管理系统 V1.0

DeskGuard 是面向虚拟桌面部署的本地配置证据核查与整改工作台。
本版不创建虚拟机、不连接目标设备、不扫描漏洞、不执行策略下发。
`mfa=true` 等字段是用户录入的证据，不是软件已验证目标真实状态。

## 功能

管理域；四角色资产登记；严格 JSON/CSV 导入；配置版本与摘要；
12条可配置的示例规则及不可变基线；固定快照核查；通过/不通过/待补证/
不适用/未启用分类；整改工单及同规则复测关闭；前后对比；HTML/JSON/CSV
报告；本地审计链检查与SQLite备份；单管理员口令和会话管理。

与前一密态向量检索项目不同，本版没有向量检索或同态加密。
通用登录、部分存储和请求体限额代码适配自用户提供的 cr2，来源见通知。
核查、版本、规则、整改和界面是按此次需求实现的独立业务逻辑。

## 安装和运行

在项目根目录执行（Python 3.13，Linux x86-64为Runner验收环境）：

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m deskguard init --username admin
python -m deskguard doctor
python -m deskguard serve --port 8768
```

浏览器打开 `http://127.0.0.1:8768`。初始化交互输入至少12位口令，无默认口令。
需要自动测试时可通过 `DESKGUARD_ADMIN_PASSWORD` 环境变量注入临时测试口令；
正式使用避免写入 shell 历史、代码、日志或共享文档。口令只按PBKDF2摘要保存。

默认数据目录 `~/.deskguard`，可用 `DESKGUARD_HOME` 指定独立目录。
`run.sh` / `run.bat` 启动已有环境，不自动安装依赖、不自动初始化管理员。
Windows启动脚本只作为便捷入口，不据此宣称完成Windows实机测试。
程序仅监听127.0.0.1。不要改作公网服务；本版无多租户权限与远程代理。

## 真实验收

```sh
python -m pip install -r requirements-dev.txt
python -m compileall -q deskguard tests tools
node --check deskguard/static/app.js
python -m pytest -q --junitxml=.evidence/tests.xml
python -m playwright install --with-deps chromium
python tools/browser_test.py --output .evidence/browser
```

GitHub Runner保存测试XML、日志、源码SHA-256清单、实际浏览器截图、逐步说明、
导出文件与浏览器摘要。以测试文件内容和退出码判断，不只看绿色工作流图标。
截图中的资产和业务记录均为合成测试，不包含公司真实资产。
`tools/browser_test.py --chromium /path/to/chromium` 可指定本机浏览器。

## 输入范围与规则

单域1—200条资产；角色为host/controller/desktop/terminal。编号在域内唯一，
编号及角色创建后不可更改。JSON顶层资产数组；CSV表头必须严格对应模板。
布尔字段只接受true/false/null；数字字段是0—36500整数或null。未知字段、
重复JSON键、类型强制转换、重复编号均拒绝。整批写入同一事务，不保留部分输入。
资产文本不能有控制字符或以=、+、-、@开头，避免CSV公式注入与静默改写。

配置字段：mfa、tls、audit_enabled、log_days、idle_minutes、usb_blocked、
clipboard_blocked、image_signed、backup_days、patch_days、antimalware、admin_separated。
未提供与null均为待补证，导出仍保留二者区别。idle_minutes=0表示未自动锁定，
不满足启用空闲锁定的规则。其他数值0按字段含义处理（如刚完成备份距今0天）。

12条规则是本应用的示例策略，不来自保密标准或强制合规标准。可改变阈值和启用状态，
不能加载任意代码、脚本或表达式。保存后不可原地修改，调整通过新建/复制基线完成。
证据完整率=已知项/适用项，已知项通过率=通过/已知项，证据评分=通过权重/适用权重。
待补证纳入评分分母；不适用、未启用不纳入；分母0返回null而非100%。

## 整改证据条件

工单只从不通过项建立。状态为待处理→处理中→待验证→已关闭。
关闭必须引用新的已完成核查，同域、相同规则内容、同资产与同规则，且资产配置版本
晚于原问题版本，该项实际核查结果为通过。不能靠降低阈值、禁用规则或复用旧任务关闭。
原始失败任务与快照保持不变。待补证须先补录配置后新建核查，不冒充已确认问题工单。

## 备份与边界

备份写入数据目录backups；包含业务记录与账号摘要，按组织规则保管。
本版没有在线恢复接口。审计链只能检测本地记录一致性；有数据库写权限者可重算整链，
无外部锚定，不宣称不可篡改。报告HTML供浏览器查看/打印，无后端PDF生成接口。
不同规则内容的任务不直接比较；新增或缺少资产项单独列示，不当成整改修复。

## 软著材料

三份Word在交付压缩包中，涉及公司填报底稿，不上传本公开仓库。
源码Word为应用文件前1500和后1500个非空行，60页每页50行，无行号。
完整代码不止3000行。测试、构建工具及第三方依赖不计入应用源码数量。
开发与权属、人的实质性贡献、公司主体和发表事实由申请人核实，不作获证保证。
