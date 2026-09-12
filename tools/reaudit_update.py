from pathlib import Path
import hashlib

ORIGINAL = {'service.py': '072511bec9d641a91312381da7eee3e8c75395f9e7b11dccbab21b074e414b38', 'security.py': '911ca62e1be6fed6493f31e157d37b8ab00c34ad6a71962a4b112e3db0b66aba', 'importing.py': '174adfa8f02e03119c0b0fb4a3c5c5f7ca569054b6e191223e14c8c54badb4da', 'transport.py': 'c28124432184d860b53fe52afd94bdc1230ee452f4a16f4ea4f2dab7b537be84', 'api.py': '53faacc69a1ea28c5d4bba9c69433de8d44fb6265e75b4918b31684143753328'}
EXPECTED = {'service.py': 'fcc0296f36a6dadf1487d0c5fc9822ab1b26854805ec7e7b0677026dae29a943', 'security.py': '986d464120b9e73628ce0a269e6bcbc1f4342c966fb7b6af6b4de7453de1691e', 'importing.py': 'd19e2c2a9765fea20f9ba01559bcf77fcf9a14317b36fd10196071e033d03b6a', 'transport.py': '543227669f169b6d15356d1241d5adbb9d89a755fe23889cdf907b205c7a81ca', 'api.py': 'f6d1befd03bbe518a4ff587d0e667d418a91ec9147855eafb256d0d38cf901fa'}
for name, sha in ORIGINAL.items():
    assert hashlib.sha256((Path('deskguard') / name).read_bytes()).hexdigest() == sha, name
r=Path('deskguard')
p=r/'service.py';s=p.read_text()
old='''            if row["status"] != "resolved":
                raise DomainError("工单必须先处理并提交为待验证")
            conn.execute(
'''
new='''            if row["status"] != "resolved":
                raise DomainError("工单必须先处理并提交为待验证")
            # Check under the same write transaction as closure: a later asset
            # edit must not be hidden by a superseded passing snapshot.
            current = require(conn.execute(
                "SELECT revision FROM assets WHERE id=?", (row["asset_id"],)
            ).fetchone(), "工单资产不存在")
            if current["revision"] != right["asset_revision"]:
                raise DomainError("复测证据已过期，请使用该资产当前版本重新核查")
            conn.execute(
'''
assert old in s;s=s.replace(old,new);p.write_text(s)
p=r/'security.py';s=p.read_text()
old='''            conn.execute(
                "UPDATE users SET password_hash=?,updated_at=? "
                "WHERE username=?",
                (encoded, utc_now(), identity.username),
            )
            conn.execute(
'''
new='''            changed = conn.execute(
                "UPDATE users SET password_hash=?,updated_at=? "
                "WHERE username=? AND password_hash=?",
                (encoded, utc_now(), identity.username, row["password_hash"]),
            ).rowcount
            if changed != 1:
                raise AccessError("口令已被其他请求修改，请重新登录", 403)
            conn.execute(
'''
assert old in s;s=s.replace(old,new)
s=s.replace('if not provided or not hmac.compare_digest(identity.csrf, provided):',
'''if not provided or not provided.isascii() or not hmac.compare_digest(identity.csrf, provided):''')
p.write_text(s)
p=r/'importing.py';s=p.read_text()
s=s.replace('def parse_csv(text: str) -> list[dict]:\n    reader = csv.DictReader(io.StringIO(text.lstrip("\\ufeff"), newline=""))',
'''def parse_csv(text: str) -> list[dict]:
    try:
        return _parse_csv_rows(text)
    except csv.Error as exc:
        raise ValueError("CSV格式错误或字段过长，请按模板检查引号及字段长度") from exc


def _parse_csv_rows(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text.lstrip("\\ufeff"), newline=""), strict=True)''')
assert 'strict=True' in s;p.write_text(s)
p=r/'transport.py';s=p.read_text();s=s.replace('from fastapi.responses import JSONResponse',
'''import json
from fastapi.responses import JSONResponse


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON对象存在重复键")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("JSON数字必须为有限值")


def validate_json(body):
    value = json.loads(body, object_pairs_hook=unique_object,
                       parse_constant=reject_constant)
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            item.encode("utf-8")  # Reject unpaired Unicode surrogates.
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)''')
old='''        delivered = False
'''
new='''        headers = dict(scope.get("headers", []))
        media = headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower()
        if body and (not media or media == b"application/json" or media.endswith(b"+json")):
            try:
                validate_json(body)
            except (ValueError, UnicodeError, RecursionError):
                # Never echo raw bodies, keys or credential values in errors.
                response = JSONResponse({"detail": "JSON格式、重复键或字符编码无效"}, 422)
                await response(scope, receive, send)
                return
        delivered = False
'''
assert old in s;s=s.replace(old,new);p.write_text(s)
p=r/'api.py';s=p.read_text()
old='''            expected, actual = urlsplit(str(request.base_url)), urlsplit(origin)
            if (expected.scheme, expected.netloc) != (actual.scheme, actual.netloc):
                return JSONResponse({"detail": "拒绝跨站写入"}, status_code=403)
'''
new='''            try:
                expected, actual = urlsplit(str(request.base_url)), urlsplit(origin)
                valid_origin = (
                    (expected.scheme, expected.netloc) == (actual.scheme, actual.netloc)
                    and not actual.path and not actual.query and not actual.fragment
                )
            except ValueError:
                valid_origin = False
            if not valid_origin:
                return JSONResponse({"detail": "拒绝跨站写入"}, status_code=403)
'''
assert old in s;s=s.replace(old,new);p.write_text(s)

for name, sha in EXPECTED.items():
    assert hashlib.sha256((r / name).read_bytes()).hexdigest() == sha, name
