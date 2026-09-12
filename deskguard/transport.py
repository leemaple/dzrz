"""Bounded request buffering adapted from the user-provided cr2 code."""
import json
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
            stack.extend(item)


class BodyLimitMiddleware:
    def __init__(self, app, maximum: int):
        self.app = app
        self.maximum = maximum

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.maximum:
                response = JSONResponse({"detail": "请求体超过限制"}, 413)
                await response(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        headers = dict(scope.get("headers", []))
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
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        await self.app(scope, replay, send)

