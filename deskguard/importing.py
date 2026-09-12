"""Atomic asset import and lossless typed CSV round trips."""
import csv
import io
import json
from .catalog import CONFIG_FIELDS
from .models import AssetData

COLUMNS = ["asset_key", "name", "role", "owner", "zone", *CONFIG_FIELDS]
MAX_ASSETS = 200


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON对象含重复键：" + key)
        result[key] = value
    return result


def parse_json(text: str) -> list[dict]:
    try:
        raw = json.loads(text, object_pairs_hook=reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON格式错误：第{exc.lineno}行") from exc
    if not isinstance(raw, list):
        raise ValueError("JSON顶层必须是资产数组")
    return validate_records(raw)


def parse_csv(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff"), newline=""))
    if reader.fieldnames != COLUMNS:
        raise ValueError("CSV表头及顺序必须与下载模板一致")
    records = []
    for line, row in enumerate(reader, 2):
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"CSV第{line}行列数不匹配")
        if len(records) >= MAX_ASSETS:
            raise ValueError("每次导入不得超过200条资产")
        config = {}
        for field, (_, kind) in CONFIG_FIELDS.items():
            cell = row[field].strip()
            if not cell:
                continue
            if cell == "null":
                config[field] = None
            elif kind == "bool":
                if cell not in {"true", "false"}:
                    raise ValueError(f"CSV第{line}行{field}须为true/false/null")
                config[field] = cell == "true"
            else:
                if not cell.isascii() or not cell.isdigit():
                    raise ValueError(f"CSV第{line}行{field}须为非负整数")
                config[field] = int(cell)
        records.append({
            key: row[key] for key in COLUMNS[:5]
        } | {"config": config})
    return validate_records(records)


def validate_records(records) -> list[dict]:
    if not 1 <= len(records) <= MAX_ASSETS:
        raise ValueError("资产数组需含1至200条记录")
    result = [AssetData.model_validate(item).model_dump() for item in records]
    keys = [item["asset_key"] for item in result]
    if len(set(keys)) != len(keys):
        raise ValueError("同一导入文件存在重复资产编号")
    return result


def parse_import(kind: str, text: str) -> list[dict]:
    if kind == "json":
        return parse_json(text)
    if kind == "csv":
        return parse_csv(text)
    raise ValueError("不支持的导入格式")


def formula_safe(value) -> str:
    text = str(value)
    if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def assets_csv(records: list[dict]) -> str:
    """Typed CSV: empty cell is absent, null is explicit unknown.

    AssetData rejects formula-prefixed metadata; numeric and boolean fields
    are validated before persistence. Import/export preserves their types.
    """
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    for record in records:
        row = {key: record[key] for key in COLUMNS[:5]}
        for field in CONFIG_FIELDS:
            if field in record["config"]:
                row[field] = json.dumps(record["config"][field])
        writer.writerow(row)
    return output.getvalue()


def demo_assets() -> list[dict]:
    """Public synthetic fixtures; never machine-scanned or customer data."""
    common = {
        "tls": True, "audit_enabled": True, "patch_days": 10,
        "mfa": True, "log_days": 180, "backup_days": 3,
        "admin_separated": True, "image_signed": True,
        "idle_minutes": 10, "usb_blocked": True,
        "clipboard_blocked": True, "antimalware": True,
    }
    rows = []
    for key, name, role, patch in [
        ("HOST-01", "合成承载主机01", "host", {"mfa": False, "backup_days": 20}),
        ("CTRL-01", "合成管理控制器01", "controller", {"log_days": 30}),
        ("VDI-01", "合成办公桌面01", "desktop", {"usb_blocked": False}),
        ("TERM-01", "合成接入终端01", "terminal", {"antimalware": None}),
    ]:
        rows.append({
            "asset_key": key, "name": name, "role": role,
            "owner": "演示运维组", "zone": "合成测试域",
            "config": common | patch,
        })
    return rows
