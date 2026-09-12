"""Strict input contracts shared by imports and interactive editing."""
from datetime import date
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .catalog import CONFIG_FIELDS, RULE_INDEX


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Login(Model):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class PasswordChange(Model):
    model_config = ConfigDict(extra="forbid")
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class SpaceCreate(Model):
    name: str = Field(min_length=2, max_length=60)
    description: str = Field(default="", max_length=500)


class SpaceState(Model):
    status: Literal["active", "archived"]


class AssetData(Model):
    asset_key: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,40}$")
    name: str = Field(min_length=2, max_length=80)
    role: Literal["host", "controller", "desktop", "terminal"]
    owner: str = Field(min_length=1, max_length=60)
    zone: str = Field(min_length=1, max_length=60)
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("asset_key", "name", "owner", "zone")
    @classmethod
    def safe_asset_text(cls, value):
        if any(ord(char) < 32 for char in value):
            raise ValueError("资产文本不能包含控制字符")
        if value.lstrip().startswith(("=", "+", "-", "@")):
            raise ValueError("资产文本不能以表格公式触发字符开头")
        return value

    @field_validator("config")
    @classmethod
    def valid_config(cls, value):
        unknown = set(value) - set(CONFIG_FIELDS)
        if unknown:
            raise ValueError("不支持的配置字段：" + ",".join(sorted(unknown)))
        for key, val in value.items():
            if val is None:
                continue
            kind = CONFIG_FIELDS[key][1]
            if kind == "bool" and type(val) is not bool:
                raise ValueError(key + " 必须是布尔值或null")
            if kind == "int" and (type(val) is not int or not 0 <= val <= 36500):
                raise ValueError(key + " 必须是0至36500的整数或null")
        return value


class AssetCreate(AssetData):
    space_id: str = Field(min_length=1, max_length=40)


class AssetUpdate(AssetData):
    expected_revision: int = Field(ge=1, strict=True)


class ImportRequest(Model):
    space_id: str = Field(min_length=1, max_length=40)
    format: Literal["json", "csv"]
    content: str = Field(min_length=1, max_length=1_000_000)


class DemoRequest(Model):
    space_id: str = Field(min_length=1, max_length=40)


class RuleOverride(Model):
    id: str
    enabled: bool = Field(default=True, strict=True)
    expected: Any = None

    @field_validator("id")
    @classmethod
    def known_rule(cls, value):
        if value not in RULE_INDEX:
            raise ValueError("规则编号不存在")
        return value


class BaselineCreate(Model):
    name: str = Field(min_length=2, max_length=60)
    description: str = Field(default="", max_length=500)
    overrides: list[RuleOverride] = Field(default_factory=list, max_length=12)

    @field_validator("overrides")
    @classmethod
    def unique_rules(cls, value):
        if len({item.id for item in value}) != len(value):
            raise ValueError("规则不能重复覆盖")
        return value


class JobCreate(Model):
    name: str = Field(min_length=2, max_length=80)
    space_id: str = Field(min_length=1, max_length=40)
    baseline_id: str = Field(min_length=1, max_length=40)


class CompareRequest(Model):
    before: str = Field(min_length=1, max_length=40)
    after: str = Field(min_length=1, max_length=40)


class TicketCreate(Model):
    job_id: str = Field(min_length=1, max_length=40)
    asset_id: str = Field(min_length=1, max_length=40)
    rule_id: str = Field(min_length=1, max_length=40)
    owner: str = Field(min_length=1, max_length=60)
    due_date: date
    note: str = Field(default="", max_length=1000)


class TicketTransition(Model):
    status: Literal["in_progress", "resolved", "open"]
    note: str = Field(min_length=5, max_length=1000)


class TicketVerify(Model):
    job_id: str = Field(min_length=1, max_length=40)
    note: str = Field(min_length=5, max_length=1000)
