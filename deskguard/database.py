"""Transactional storage and locally verifiable audit records.

Transaction/hash utilities are adapted from the user's cr2 code. The domain
schema, revision records, evaluation jobs, and tickets are new to DeskGuard.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
import hashlib
import json
import os
import sqlite3
import uuid


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return prefix + "_" + uuid.uuid4().hex[:16]


def canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL REFERENCES users(username),
    csrf TEXT NOT NULL,
    expires_at REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (
    subject TEXT PRIMARY KEY,
    failures INTEGER NOT NULL,
    locked_until REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS spaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','archived')),
    revision INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL REFERENCES spaces(id),
    asset_key TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('host','controller','desktop','terminal')),
    owner TEXT NOT NULL,
    zone TEXT NOT NULL,
    config_json TEXT NOT NULL,
    revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(space_id,asset_key)
);
CREATE TABLE IF NOT EXISTS revisions (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES assets(id),
    revision INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(asset_id,revision)
);
CREATE TABLE IF NOT EXISTS baselines (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    rules_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    space_id TEXT NOT NULL REFERENCES spaces(id),
    baseline_id TEXT NOT NULL REFERENCES baselines(id),
    status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed')),
    snapshot_json TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    result_json TEXT,
    result_hash TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    space_id TEXT NOT NULL REFERENCES spaces(id),
    asset_id TEXT NOT NULL REFERENCES assets(id),
    rule_id TEXT NOT NULL,
    title TEXT NOT NULL,
    severity TEXT NOT NULL,
    owner TEXT NOT NULL,
    due_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('open','in_progress','resolved','closed')),
    note TEXT NOT NULL,
    verify_job_id TEXT REFERENCES jobs(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(job_id,asset_id,rule_id)
);
CREATE TABLE IF NOT EXISTS ticket_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL REFERENCES tickets(id),
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assets_space ON assets(space_id);
CREATE INDEX IF NOT EXISTS idx_jobs_space ON jobs(space_id);
CREATE INDEX IF NOT EXISTS idx_tickets_space ON tickets(space_id);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit(action);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def read(self):
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.read() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO metadata VALUES (?,?)", ("schema", "1")
            )
            conn.commit()
        if os.name == "posix":
            self.path.chmod(0o600)

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        with self.read() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.read() as conn:
            return [dict(row) for row in conn.execute(sql, params)]

    @staticmethod
    def audit(conn, actor, action, entity, detail):
        previous = conn.execute(
            "SELECT entry_hash FROM audit ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        payload = {
            "timestamp": utc_now(), "actor": actor, "action": action,
            "entity": entity, "detail": detail,
            "previous_hash": previous[0] if previous else "0" * 64,
        }
        checksum = digest(payload)
        conn.execute(
            "INSERT INTO audit(timestamp,actor,action,entity,detail_json,"
            "previous_hash,entry_hash) VALUES (?,?,?,?,?,?,?)",
            (payload["timestamp"], actor, action, entity, canonical(detail),
             payload["previous_hash"], checksum),
        )
        return checksum

    def verify_audit(self):
        previous = "0" * 64
        rows = self.all("SELECT * FROM audit ORDER BY seq")
        for row in rows:
            payload = {
                "timestamp": row["timestamp"], "actor": row["actor"],
                "action": row["action"], "entity": row["entity"],
                "detail": json.loads(row["detail_json"]),
                "previous_hash": row["previous_hash"],
            }
            if row["previous_hash"] != previous or digest(payload) != row["entry_hash"]:
                return {"valid": False, "first_invalid": row["seq"], "count": len(rows)}
            previous = row["entry_hash"]
        return {"valid": True, "count": len(rows), "head": previous}

    def recover(self):
        with self.transaction() as conn:
            changed = conn.execute(
                "UPDATE jobs SET status='failed',error=?,finished_at=? "
                "WHERE status='running'",
                ("进程中断，请新建任务复测", utc_now()),
            ).rowcount
            if changed:
                self.audit(conn, "system", "job.recover", "runtime", {"count": changed})
        return changed

    def backup(self, directory: Path) -> dict:
        directory.mkdir(parents=True, exist_ok=True)
        name = new_id("backup") + ".sqlite3"
        target = directory / name
        try:
            with self.read() as source:
                destination = sqlite3.connect(target)
                try:
                    source.backup(destination)
                    check = destination.execute("PRAGMA integrity_check").fetchone()[0]
                    if check != "ok":
                        raise ValueError("备份一致性检查失败")
                finally:
                    destination.close()
            if os.name == "posix":
                target.chmod(0o600)
            return {
                "filename": name, "size": target.stat().st_size,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        except BaseException:
            target.unlink(missing_ok=True)
            raise
