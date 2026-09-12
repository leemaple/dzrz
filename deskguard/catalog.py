"""Application-owned demonstration rules, not statutory compliance clauses."""
from copy import deepcopy

ROLE_LABELS = {
    "host": "承载主机", "controller": "管理控制器",
    "desktop": "虚拟桌面", "terminal": "接入终端",
}
CONFIG_FIELDS = {
    "mfa": ("管理多因素认证", "bool"),
    "tls": ("传输加密", "bool"),
    "audit_enabled": ("操作审计", "bool"),
    "log_days": ("日志保留天数", "int"),
    "idle_minutes": ("空闲锁定分钟数", "int"),
    "usb_blocked": ("USB重定向限制", "bool"),
    "clipboard_blocked": ("剪贴板重定向限制", "bool"),
    "image_signed": ("镜像签名校验", "bool"),
    "backup_days": ("最近备份距今天数", "int"),
    "patch_days": ("最近补丁距今天数", "int"),
    "antimalware": ("终端防护启用", "bool"),
    "admin_separated": ("管理账号分离", "bool"),
}
ALL_ROLES = list(ROLE_LABELS)
RULES = [
    {
        "id": "DG-01", "name": "管理入口启用多因素认证",
        "field": "mfa", "operator": "eq", "expected": True,
        "roles": ["host", "controller"], "severity": "high", "weight": 10,
        "guidance": "核对管理入口配置，启用多因素认证后更新配置证据。",
    },
    {
        "id": "DG-02", "name": "数据传输启用加密",
        "field": "tls", "operator": "eq", "expected": True,
        "roles": ALL_ROLES, "severity": "high", "weight": 10,
        "guidance": "核对客户端和服务端传输设置，记录有效的加密配置。",
    },
    {
        "id": "DG-03", "name": "启用操作审计",
        "field": "audit_enabled", "operator": "eq", "expected": True,
        "roles": ALL_ROLES, "severity": "high", "weight": 10,
        "guidance": "在目标组件启用审计并核对日志输出，再补录配置状态。",
    },
    {
        "id": "DG-04", "name": "日志保留周期满足自定义阈值",
        "field": "log_days", "operator": "ge", "expected": 180,
        "roles": ["host", "controller"], "severity": "medium", "weight": 5,
        "guidance": "根据组织自定策略调整日志保留周期；本阈值不是合规认定。",
    },
    {
        "id": "DG-05", "name": "空闲锁定时间不超过阈值",
        "field": "idle_minutes", "operator": "le", "expected": 15,
        "roles": ["desktop", "terminal"], "severity": "medium", "weight": 5,
        "guidance": "设置合理空闲锁定时间；0表示未自动锁定，本项不通过。",
    },
    {
        "id": "DG-06", "name": "限制USB重定向",
        "field": "usb_blocked", "operator": "eq", "expected": True,
        "roles": ["desktop", "terminal"], "severity": "medium", "weight": 5,
        "guidance": "按业务授权范围限制USB重定向并记录配置。",
    },
    {
        "id": "DG-07", "name": "限制剪贴板重定向",
        "field": "clipboard_blocked", "operator": "eq", "expected": True,
        "roles": ["desktop", "terminal"], "severity": "medium", "weight": 5,
        "guidance": "核对桌面与终端剪贴板策略的一致性。",
    },
    {
        "id": "DG-08", "name": "基础镜像启用签名校验",
        "field": "image_signed", "operator": "eq", "expected": True,
        "roles": ["host", "controller", "desktop"], "severity": "high", "weight": 10,
        "guidance": "核对镜像分发与启动前校验配置，本软件不代验镜像签名。",
    },
    {
        "id": "DG-09", "name": "备份记录未超出阈值",
        "field": "backup_days", "operator": "le", "expected": 7,
        "roles": ["host", "controller"], "severity": "medium", "weight": 5,
        "guidance": "核实最近备份与恢复演练记录，补录真实天数。",
    },
    {
        "id": "DG-10", "name": "补丁维护周期未超出阈值",
        "field": "patch_days", "operator": "le", "expected": 30,
        "roles": ALL_ROLES, "severity": "medium", "weight": 5,
        "guidance": "依据维护窗口核对补丁记录，不代表已排除全部漏洞。",
    },
    {
        "id": "DG-11", "name": "终端防护处于启用状态",
        "field": "antimalware", "operator": "eq", "expected": True,
        "roles": ["desktop", "terminal"], "severity": "medium", "weight": 5,
        "guidance": "核对终端防护运行配置，异常状态应先处置后补录。",
    },
    {
        "id": "DG-12", "name": "管理账号与普通账号分离",
        "field": "admin_separated", "operator": "eq", "expected": True,
        "roles": ["host", "controller"], "severity": "high", "weight": 10,
        "guidance": "核查账号职责分离情况，不将填报值当作权限渗透测试结果。",
    },
]
RULE_INDEX = {rule["id"]: rule for rule in RULES}


def default_rules() -> list[dict]:
    return [dict(deepcopy(rule), enabled=True) for rule in RULES]


def public_catalog() -> dict:
    return {
        "roles": ROLE_LABELS,
        "fields": {key: {"label": value[0], "type": value[1]}
                   for key, value in CONFIG_FIELDS.items()},
        "rules": default_rules(),
        "scope": "核对用户填报配置，不扫描主机，不认定法定合规或产品安全。",
    }
