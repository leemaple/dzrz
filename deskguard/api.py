"""Loopback-only HTTP API with CSRF, input validation, and atomic operations."""
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
import json
import sqlite3
from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from . import NAME, VERSION
from .catalog import public_catalog
from .config import Settings
from .database import Database
from .importing import assets_csv, demo_assets
from .models import (
    Login, PasswordChange, SpaceCreate, SpaceState, AssetCreate, AssetUpdate,
    ImportRequest, DemoRequest, BaselineCreate, JobCreate, CompareRequest,
    TicketCreate, TicketTransition, TicketVerify,
)
from .reporting import findings_csv, audit_csv, tickets_csv, report_json, report_html
from .security import Auth, AccessError, Identity, COOKIE_NAME
from .service import Service, DomainError
from .transport import BodyLimitMiddleware

STATIC = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.prepare()
    db = Database(settings.database)
    db.initialize()
    auth = Auth(db, settings.session_seconds)
    service = Service(db, settings.backups)

    @asynccontextmanager
    async def lifespan(app):
        db.recover()
        yield

    app = FastAPI(title=NAME, version=VERSION, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.database = db
    app.state.auth = auth
    app.state.service = service
    app.state.settings = settings
    app.add_middleware(BodyLimitMiddleware, maximum=settings.max_body_bytes)
    app.add_middleware(TrustedHostMiddleware,
                       allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])

    @app.middleware("http")
    async def browser_security(request, call_next):
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            try:
                expected, actual = urlsplit(str(request.base_url)), urlsplit(origin)
                valid_origin = (
                    (expected.scheme, expected.netloc) == (actual.scheme, actual.netloc)
                    and not actual.path and not actual.query and not actual.fragment
                )
            except ValueError:
                valid_origin = False
            if not valid_origin:
                return JSONResponse({"detail": "拒绝跨站写入"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    @app.exception_handler(AccessError)
    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.exception_handler(ValueError)
    async def input_error(request, exc):
        return JSONResponse({"detail": str(exc)[:1000]}, status_code=422)

    @app.exception_handler(sqlite3.IntegrityError)
    async def conflict(request, exc):
        return JSONResponse({"detail": "名称、资产编号重复或数据关联冲突；未部分保存"}, status_code=409)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        errors = [".".join(str(p) for p in e["loc"]) + ": " + e["msg"]
                  for e in exc.errors()]
        return JSONResponse({"detail": "；".join(errors)[:1000]}, status_code=422)

    def identity(request: Request) -> Identity:
        result = auth.authenticate(request.cookies.get(COOKIE_NAME))
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            auth.check_csrf(result, request.headers.get("X-CSRF-Token"))
        return result

    def attachment(text, name, kind):
        return Response(text, media_type=kind,
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/health")
    def health():
        return {"status": "ok", "software": NAME, "version": VERSION}

    @app.get("/api/setup-status")
    def setup_status():
        return {"initialized": auth.is_initialized()}

    @app.post("/api/login")
    def login(payload: Login, response: Response):
        token, who = auth.login(payload.username, payload.password)
        response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="strict",
                            max_age=settings.session_seconds)
        return auth.public_identity(who)

    @app.get("/api/me")
    def me(who: Identity = Depends(identity)):
        return auth.public_identity(who)

    @app.post("/api/logout")
    def logout(response: Response, who: Identity = Depends(identity)):
        auth.logout(who)
        response.delete_cookie(COOKIE_NAME)
        return {"ok": True}

    @app.post("/api/password")
    def password(payload: PasswordChange, response: Response,
                 who: Identity = Depends(identity)):
        auth.change_password(who, payload.old_password, payload.new_password)
        response.delete_cookie(COOKIE_NAME)
        return {"ok": True, "sessions_revoked": True}

    @app.get("/api/catalog")
    def catalog(who: Identity = Depends(identity)):
        return public_catalog()

    @app.get("/api/overview")
    def overview(who: Identity = Depends(identity)):
        return service.overview()

    @app.get("/api/spaces")
    def spaces(who: Identity = Depends(identity)):
        return service.spaces()

    @app.post("/api/spaces", status_code=201)
    def create_space(payload: SpaceCreate, who: Identity = Depends(identity)):
        return service.create_space(payload.model_dump(), who.username)

    @app.patch("/api/spaces/{key}")
    def state(key: str, payload: SpaceState, who: Identity = Depends(identity)):
        return service.set_space_state(key, payload.status, who.username)

    @app.get("/api/assets")
    def assets(space_id: str, who: Identity = Depends(identity)):
        return service.assets(space_id)

    @app.get("/api/assets/{key}")
    def asset(key: str, who: Identity = Depends(identity)):
        return service.asset(key)

    @app.post("/api/assets", status_code=201)
    def create_asset(payload: AssetCreate, who: Identity = Depends(identity)):
        return service.create_asset(payload.model_dump(), who.username)

    @app.put("/api/assets/{key}")
    def update_asset(key: str, payload: AssetUpdate, who: Identity = Depends(identity)):
        return service.update_asset(key, payload.model_dump(), who.username)

    @app.get("/api/assets/{key}/revisions")
    def revisions(key: str, who: Identity = Depends(identity)):
        return service.revisions(key)

    @app.post("/api/assets-import", status_code=201)
    def import_assets(payload: ImportRequest, who: Identity = Depends(identity)):
        return service.import_assets(payload.model_dump(), who.username)

    @app.post("/api/demo-assets", status_code=201)
    def demo(payload: DemoRequest, who: Identity = Depends(identity)):
        return service.demo(payload.space_id, who.username)

    @app.get("/api/template/{kind}")
    def template(kind: str, who: Identity = Depends(identity)):
        records = demo_assets()
        service.record_export(who.username, "template", kind)
        if kind == "json":
            return attachment(json.dumps(records, ensure_ascii=False, indent=2),
                              "assets-template.json", "application/json")
        if kind == "csv":
            return attachment(assets_csv(records), "assets-template.csv", "text/csv")
        raise DomainError("导出格式不存在", 404)

    @app.get("/api/spaces/{key}/export/{kind}")
    def export_assets(key: str, kind: str, who: Identity = Depends(identity)):
        rows = service.assets(key)
        records = [{k: row[k] for k in ("asset_key", "name", "role", "owner", "zone", "config")}
                   for row in rows]
        service.record_export(who.username, key, kind)
        if kind == "json":
            return attachment(
                json.dumps(records, ensure_ascii=False, indent=2),
                "assets.json", "application/json",
            )
        if kind == "csv":
            return attachment(assets_csv(records), "assets.csv", "text/csv")
        raise DomainError("导出格式不存在", 404)

    @app.get("/api/baselines")
    def baselines(who: Identity = Depends(identity)):
        return service.baselines()

    @app.post("/api/baselines", status_code=201)
    def baseline(payload: BaselineCreate, who: Identity = Depends(identity)):
        return service.create_baseline(payload.model_dump(), who.username)

    @app.get("/api/jobs")
    def jobs(space_id: str | None = None, who: Identity = Depends(identity)):
        return service.jobs(space_id)

    @app.post("/api/jobs", status_code=201)
    def create_job(payload: JobCreate, who: Identity = Depends(identity)):
        return service.create_job(payload.model_dump(), who.username)

    @app.get("/api/jobs/{key}")
    def job(key: str, who: Identity = Depends(identity)):
        return service.job(key)

    @app.post("/api/jobs/{key}/execute")
    def execute(key: str, who: Identity = Depends(identity)):
        return service.execute(key, who.username)

    @app.get("/api/jobs/{key}/export/{kind}")
    def export_job(key: str, kind: str, who: Identity = Depends(identity)):
        data = service.completed_job(key)
        service.record_export(who.username, key, kind)
        if kind == "json":
            return attachment(report_json(data), "check-result.json", "application/json")
        if kind == "csv":
            return attachment(findings_csv(data), "check-result.csv", "text/csv")
        if kind == "html":
            return HTMLResponse(report_html(data))
        raise DomainError("导出格式不存在", 404)

    @app.post("/api/compare")
    def compare(payload: CompareRequest, who: Identity = Depends(identity)):
        result = service.compare(payload.before, payload.after)
        service.record_export(who.username, payload.after, "compare")
        return result

    @app.get("/api/tickets")
    def tickets(who: Identity = Depends(identity)):
        return service.tickets()

    @app.post("/api/tickets", status_code=201)
    def create_ticket(payload: TicketCreate, who: Identity = Depends(identity)):
        return service.create_ticket(payload.model_dump(mode="json"), who.username)

    @app.get("/api/tickets/{key}")
    def ticket(key: str, who: Identity = Depends(identity)):
        return service.ticket(key)

    @app.post("/api/tickets/{key}/transition")
    def transition(key: str, payload: TicketTransition, who: Identity = Depends(identity)):
        return service.transition_ticket(key, payload.status, payload.note, who.username)

    @app.post("/api/tickets/{key}/verify")
    def verify(key: str, payload: TicketVerify, who: Identity = Depends(identity)):
        return service.verify_ticket(key, payload.job_id, payload.note, who.username)

    @app.get("/api/tickets-export")
    def export_tickets(who: Identity = Depends(identity)):
        service.record_export(who.username, "tickets", "csv")
        return attachment(tickets_csv(service.tickets()), "tickets.csv", "text/csv")

    @app.get("/api/audit")
    def audit(action: str = Query(default="", max_length=64), who: Identity = Depends(identity)):
        return service.audit_rows(action)

    @app.get("/api/audit-export")
    def export_audit(who: Identity = Depends(identity)):
        service.record_export(who.username, "audit", "csv")
        return attachment(audit_csv(service.audit_rows()), "audit.csv", "text/csv")

    @app.get("/api/audit-verify")
    def verify_audit(who: Identity = Depends(identity)):
        return db.verify_audit()

    @app.get("/api/system")
    def system(who: Identity = Depends(identity)):
        return {"software": NAME, "version": VERSION, "engine": "deskguard-rules-v1",
                "database": settings.database.name, "rule_count": 12,
                "max_assets_per_space": 200, "scope": "仅本地配置记录核查，无远程执行能力"}

    @app.post("/api/backup")
    def backup(who: Identity = Depends(identity)):
        return service.backup(who.username)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", media_type="text/html")

    return app
