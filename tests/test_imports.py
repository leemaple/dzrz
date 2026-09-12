import json
import pytest
from pydantic import ValidationError
from deskguard.models import AssetData, BaselineCreate
from deskguard.importing import parse_import, demo_assets, assets_csv, parse_json


@pytest.mark.parametrize("kind", ["csv", "json"])
def test_roundtrip(kind):
    rows = demo_assets()
    rows[0]["name"] = '设备,含"引号"'
    rows[1]["config"].pop("clipboard_blocked")
    rows[2]["config"]["tls"] = None
    text = assets_csv(rows) if kind == "csv" else json.dumps(rows, ensure_ascii=False)
    assert parse_import(kind, text) == rows


@pytest.mark.parametrize("text", ["{}", "[]", "[null]", '[{"x":1,"x":2}]', "not-json", "[NaN]"])
def test_bad_json(text):
    with pytest.raises((ValueError, ValidationError, TypeError)):
        parse_json(text)


@pytest.mark.parametrize("field,value", [
    ("mfa", 1), ("mfa", "true"), ("log_days", True), ("log_days", "180"),
    ("log_days", -1), ("log_days", 36501), ("secret_password", "no"),
])
def test_strict_config(field, value):
    row = demo_assets()[0]
    row["config"][field] = value
    with pytest.raises(ValidationError):
        AssetData.model_validate(row)


@pytest.mark.parametrize("text", ["=1+1", "+formula", "-formula", "@SUM(A1)", "a\tb", "a\nb"])
def test_formula_and_controls_rejected(text):
    row = demo_assets()[0]
    row["name"] = text
    with pytest.raises(ValidationError):
        AssetData.model_validate(row)


def test_duplicate_asset_ids():
    rows = demo_assets()
    rows.append(rows[0])
    with pytest.raises(ValueError):
        parse_json(json.dumps(rows))


def test_csv_bad_column_count():
    text = assets_csv(demo_assets())
    with pytest.raises(ValueError):
        parse_import("csv", text + 'bad,line\n')


def test_csv_bad_headers():
    with pytest.raises(ValueError):
        parse_import("csv", "name,role\ntest,host\n")


def test_duplicate_override():
    with pytest.raises(ValidationError):
        BaselineCreate.model_validate({"name": "demo", "overrides": [{"id": "DG-01"}, {"id": "DG-01"}]})
