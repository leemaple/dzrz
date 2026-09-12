"""Domain operations: versioned asset evidence, frozen checks, and remediation."""
from datetime import date
from pathlib import Path
import json
import sqlite3
import time
from .database import Database, canonical, digest, new_id, utc_now
from .checking import build_rules, evaluate, compare_results
from .importing import parse_import, demo_assets, MAX_ASSETS


class DomainError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def require(row, message="记录不存在"):
    if row is None:
        raise DomainError(message, 404)
    return dict(row)


def decode_asset(row: dict) -> dict:
    result = dict(row)
    result["config"] = json.loads(result.pop("config_json"))
    return result


def decode_baseline(row: dict) -> dict:
    result = dict(row)
    result["rules"] = json.loads(result.pop("rules_json"))
    if digest(result["rules"]) != result["content_hash"]:
        raise DomainError("规则基线摘要不一致", 409)
    return result


class Service:
    def __init__(self, db: Database, backups: Path):
        self.db = db
        self.backups = backups

    def active_space(self, conn, space_id):
        row = require(conn.execute(
            "SELECT * FROM spaces WHERE id=?", (space_id,)
        ).fetchone(), "管理域不存在")
        if row["status"] != "active":
            raise DomainError("管理域已归档，只允许查阅历史数据")
        return row

    def create_space(self, payload: dict, actor: str):
        key, now = new_id("sp"), utc_now()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO spaces VALUES (?,?,?,?,?,?,?)",
                (key, payload["name"], payload["description"], "active", 0, now, now,)
            )
            self.db.audit(conn, actor, "space.create", key, payload)
        return self.get_space(key)

    def get_space(self, key):
        return require(self.db.one("SELECT * FROM spaces WHERE id=?", (key,)))

    def spaces(self):
        return self.db.all(
            "SELECT s.*, (SELECT count(*) FROM assets a WHERE a.space_id=s.id) "
            "AS asset_count FROM spaces s ORDER BY created_at,id"
        )

    def set_space_state(self, key, status, actor):
        with self.db.transaction() as conn:
            row = require(conn.execute(
                "SELECT * FROM spaces WHERE id=?", (key,)
            ).fetchone())
            conn.execute(
                "UPDATE spaces SET status=?,updated_at=? WHERE id=?",
                (status, utc_now(), key),
            )
            self.db.audit(conn, actor, "space.state", key,
                          {"from": row["status"], "to": status})
        return self.get_space(key)

    def assets(self, space_id):
        self.get_space(space_id)
        return [decode_asset(row) for row in self.db.all(
            "SELECT * FROM assets WHERE space_id=? ORDER BY asset_key", (space_id,)
        )]

    def asset(self, key):
        return decode_asset(require(self.db.one("SELECT * FROM assets WHERE id=?", (key,))))

    def record_revision(self, conn, asset_id, actor):
        row = decode_asset(dict(conn.execute(
            "SELECT * FROM assets WHERE id=?", (asset_id,)
        ).fetchone()))
        conn.execute(
            "INSERT INTO revisions VALUES (?,?,?,?,?,?,?)",
            (new_id("rev"), asset_id, row["revision"], canonical(row),
             digest(row), actor, utc_now()),
        )

    def insert_assets(self, space_id, records, actor):
        keys = []
        now = utc_now()
        with self.db.transaction() as conn:
            self.active_space(conn, space_id)
            current = conn.execute(
                "SELECT count(*) FROM assets WHERE space_id=?", (space_id,)
            ).fetchone()[0]
            if current + len(records) > MAX_ASSETS:
                raise DomainError("单个管理域最多200条资产", 422)
            for row in records:
                key = new_id("ast")
                keys.append(key)
                conn.execute(
                    "INSERT INTO assets VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (key, space_id, row["asset_key"], row["name"], row["role"],
                     row["owner"], row["zone"], canonical(row["config"]), 1, now, now),
                )
                self.record_revision(conn, key, actor)
            conn.execute(
                "UPDATE spaces SET revision=revision+1,updated_at=? WHERE id=?",
                (now, space_id),
            )
            self.db.audit(conn, actor, "asset.import", space_id,
                          {"asset_ids": keys, "count": len(records)})
        return [self.asset(key) for key in keys]

    def create_asset(self, payload, actor):
        payload = dict(payload)
        space_id = payload.pop("space_id")
        return self.insert_assets(space_id, [payload], actor)[0]

    def import_assets(self, payload, actor):
        rows = parse_import(payload["format"], payload["content"])
        return self.insert_assets(payload["space_id"], rows, actor)

    def demo(self, space_id, actor):
        return self.insert_assets(space_id, demo_assets(), actor)

    def update_asset(self, key, payload, actor):
        with self.db.transaction() as conn:
            old = require(conn.execute("SELECT * FROM assets WHERE id=?", (key,)).fetchone())
            self.active_space(conn, old["space_id"])
            if old["revision"] != payload["expected_revision"]:
                raise DomainError("资产版本已变化，请刷新后重新编辑")
            # Stable identity and role are required for cross-run comparisons.
            if old["asset_key"] != payload["asset_key"] or old["role"] != payload["role"]:
                raise DomainError("资产编号及角色创建后不可更改，请登记新资产", 422)
            now = utc_now()
            conn.execute(
                "UPDATE assets SET name=?,owner=?,zone=?,config_json=?,"
                "revision=revision+1,updated_at=? WHERE id=?",
                (payload["name"], payload["owner"], payload["zone"],
                 canonical(payload["config"]), now, key),
            )
            self.record_revision(conn, key, actor)
            conn.execute(
                "UPDATE spaces SET revision=revision+1,updated_at=? WHERE id=?",
                (now, old["space_id"]),
            )
            self.db.audit(conn, actor, "asset.update", key,
                          {"from_revision": old["revision"], "to_revision": old["revision"] + 1})
        return self.asset(key)

    def revisions(self, key):
        self.asset(key)
        rows = self.db.all(
            "SELECT * FROM revisions WHERE asset_id=? ORDER BY revision", (key,)
        )
        result = []
        for row in rows:
            payload = json.loads(row.pop("payload_json"))
            result.append(row | {"payload": payload, "valid": digest(payload) == row["content_hash"]})
        return result

    def baselines(self):
        return [decode_baseline(row) for row in self.db.all(
            "SELECT * FROM baselines ORDER BY created_at,id"
        )]

    def baseline(self, key):
        return decode_baseline(require(self.db.one(
            "SELECT * FROM baselines WHERE id=?", (key,)
        )))

    def create_baseline(self, payload, actor):
        rules = build_rules(payload["overrides"])
        key = new_id("bl")
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO baselines VALUES (?,?,?,?,?,?)",
                (key, payload["name"], payload["description"], canonical(rules),
                 digest(rules), utc_now()),
            )
            self.db.audit(conn, actor, "baseline.create", key,
                          {"name": payload["name"], "hash": digest(rules)})
        return self.baseline(key)

    def create_job(self, payload, actor):
        key = new_id("job")
        with self.db.transaction() as conn:
            space = self.active_space(conn, payload["space_id"])
            baseline = decode_baseline(require(conn.execute(
                "SELECT * FROM baselines WHERE id=?", (payload["baseline_id"],)
            ).fetchone(), "基线不存在"))
            assets = [decode_asset(dict(row)) for row in conn.execute(
                "SELECT * FROM assets WHERE space_id=? ORDER BY asset_key",
                (space["id"],),
            )]
            if not assets:
                raise DomainError("管理域没有资产，不能创建空核查任务", 422)
            snapshot = {
                "schema": 1, "space_id": space["id"], "space_name": space["name"],
                "space_revision": space["revision"], "baseline": baseline,
                "assets": assets,
            }
            conn.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, payload["name"], space["id"], baseline["id"], "pending",
                 canonical(snapshot), digest(snapshot), None, None, None,
                 utc_now(), None, None),
            )
            self.db.audit(conn, actor, "job.create", key,
                          {"snapshot_hash": digest(snapshot), "assets": len(assets)})
        return self.job(key)

    def jobs(self, space_id=None):
        where, params = (" WHERE space_id=?", (space_id,)) if space_id else ("", ())
        return self.db.all(
            "SELECT id,name,space_id,baseline_id,status,snapshot_hash,"
            "created_at,started_at,finished_at,error FROM jobs" + where +
            " ORDER BY created_at DESC,id DESC", params,
        )

    def job(self, key):
        row = require(self.db.one("SELECT * FROM jobs WHERE id=?", (key,)))
        row["snapshot"] = json.loads(row.pop("snapshot_json"))
        row["snapshot_valid"] = digest(row["snapshot"]) == row["snapshot_hash"]
        text = row.pop("result_json")
        row["result"] = json.loads(text) if text else None
        row["result_valid"] = bool(row["result"] and digest(row["result"]) == row["result_hash"])
        return row

    def completed_job(self, key):
        row = self.job(key)
        if row["status"] != "completed":
            raise DomainError("只支持已完成任务")
        if not row["snapshot_valid"] or not row["result_valid"]:
            raise DomainError("任务快照或结果摘要不一致，停止使用该证据")
        return row

    def execute(self, key, actor):
        with self.db.transaction() as conn:
            row = require(conn.execute("SELECT * FROM jobs WHERE id=?", (key,)).fetchone())
            self.active_space(conn, row["space_id"])
            if row["status"] != "pending":
                raise DomainError("任务已执行或已失败，请创建新任务")
            conn.execute("UPDATE jobs SET status='running',started_at=? WHERE id=?", (utc_now(), key))
            self.db.audit(conn, actor, "job.start", key, {})
        started = time.perf_counter()
        try:
            snapshot = json.loads(row["snapshot_json"])
            if digest(snapshot) != row["snapshot_hash"]:
                raise ValueError("任务快照摘要不一致")
            if digest(snapshot["baseline"]["rules"]) != snapshot["baseline"]["content_hash"]:
                raise ValueError("基线快照摘要不一致")
            result = evaluate(snapshot)
            result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
            with self.db.transaction() as conn:
                conn.execute(
                    "UPDATE jobs SET status='completed',result_json=?,result_hash=?,"
                    "finished_at=? WHERE id=?", (canonical(result), digest(result), utc_now(), key)
                )
                self.db.audit(conn, actor, "job.complete", key, result["summary"])
        except Exception as exc:
            with self.db.transaction() as conn:
                message = str(exc)[:500]
                conn.execute("UPDATE jobs SET status='failed',error=?,finished_at=? WHERE id=?",
                             (message, utc_now(), key))
                self.db.audit(conn, actor, "job.failed", key, {"error": message})
        return self.job(key)

    def compare(self, before, after):
        return compare_results(self.completed_job(before), self.completed_job(after))

    def tickets(self):
        rows = self.db.all(
            "SELECT t.*,a.asset_key FROM tickets t JOIN assets a ON a.id=t.asset_id "
            "ORDER BY t.created_at DESC,t.id"
        )
        for row in rows:
            row["overdue"] = row["status"] != "closed" and row["due_date"] < date.today().isoformat()
        return rows

    def ticket(self, key):
        row = require(self.db.one("SELECT * FROM tickets WHERE id=?", (key,)))
        row["events"] = self.db.all(
            "SELECT * FROM ticket_events WHERE ticket_id=? ORDER BY seq", (key,)
        )
        for event in row["events"]:
            event["detail"] = json.loads(event.pop("detail_json"))
        return row

    def ticket_event(self, conn, key, actor, action, detail):
        conn.execute(
            "INSERT INTO ticket_events(ticket_id,actor,action,detail_json,created_at) "
            "VALUES (?,?,?,?,?)", (key, actor, action, canonical(detail), utc_now())
        )
        self.db.audit(conn, actor, "ticket." + action, key, detail)

    def create_ticket(self, payload, actor):
        job = self.completed_job(payload["job_id"])
        finding = next((item for item in job["result"]["findings"]
                        if item["asset_id"] == payload["asset_id"]
                        and item["rule_id"] == payload["rule_id"]), None)
        if not finding or finding["status"] != "fail":
            raise DomainError("只能从不通过项创建整改工单", 422)
        key, now = new_id("tk"), utc_now()
        with self.db.transaction() as conn:
            self.active_space(conn, job["space_id"])
            conn.execute(
                "INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, job["id"], job["space_id"], payload["asset_id"], payload["rule_id"],
                 finding["rule_name"], finding["severity"], payload["owner"],
                 str(payload["due_date"]), "open", payload["note"], None, now, now),
            )
            self.ticket_event(conn, key, actor, "create", {"finding": finding, "note": payload["note"]})
        return self.ticket(key)

    def transition_ticket(self, key, status, note, actor):
        allowed = {"open": {"in_progress"}, "in_progress": {"resolved", "open"},
                   "resolved": {"in_progress"}, "closed": set()}
        with self.db.transaction() as conn:
            row = require(conn.execute("SELECT * FROM tickets WHERE id=?", (key,)).fetchone())
            self.active_space(conn, row["space_id"])
            if status not in allowed[row["status"]]:
                raise DomainError("不允许的工单状态转换；关闭必须通过复测验证")
            conn.execute("UPDATE tickets SET status=?,note=?,updated_at=? WHERE id=?",
                         (status, note, utc_now(), key))
            self.ticket_event(conn, key, actor, "transition", {
                "from": row["status"], "to": status, "note": note,
            })
        return self.ticket(key)

    def verify_ticket(self, key, new_job_id, note, actor):
        ticket = self.ticket(key)
        old = self.completed_job(ticket["job_id"])
        new = self.completed_job(new_job_id)
        compare_results(old, new)
        lookup = lambda job: next((item for item in job["result"]["findings"]
                                   if item["asset_id"] == ticket["asset_id"]
                                   and item["rule_id"] == ticket["rule_id"]), None)
        left, right = lookup(old), lookup(new)
        if not right or right["status"] != "pass":
            raise DomainError("复测结果未通过该项，不允许关闭工单")
        if right["asset_revision"] <= left["asset_revision"]:
            raise DomainError("复测必须使用更新后的资产配置版本")
        if right["rule_hash"] != left["rule_hash"]:
            raise DomainError("复测规则发生变化，不能充当原问题修复")
        with self.db.transaction() as conn:
            row = require(conn.execute("SELECT * FROM tickets WHERE id=?", (key,)).fetchone())
            self.active_space(conn, row["space_id"])
            if row["status"] != "resolved":
                raise DomainError("工单必须先处理并提交为待验证")
            # Check under the same write transaction as closure: a later asset
            # edit must not be hidden by a superseded passing snapshot.
            current = require(conn.execute(
                "SELECT revision FROM assets WHERE id=?", (row["asset_id"],)
            ).fetchone(), "工单资产不存在")
            if current["revision"] != right["asset_revision"]:
                raise DomainError("复测证据已过期，请使用该资产当前版本重新核查")
            conn.execute(
                "UPDATE tickets SET status='closed',verify_job_id=?,note=?,updated_at=? WHERE id=?",
                (new_job_id, note, utc_now(), key),
            )
            self.ticket_event(conn, key, actor, "verified", {
                "verify_job_id": new_job_id, "asset_revision": right["asset_revision"], "note": note,
            })
        return self.ticket(key)

    def audit_rows(self, action=""):
        where, params = (" WHERE action LIKE ?", (action + "%",)) if action else ("", ())
        return self.db.all("SELECT * FROM audit" + where + " ORDER BY seq DESC LIMIT 1000", params)

    def record_export(self, actor, entity, kind):
        with self.db.transaction() as conn:
            self.db.audit(conn, actor, "export." + kind, entity, {})

    def backup(self, actor):
        result = self.db.backup(self.backups)
        with self.db.transaction() as conn:
            self.db.audit(conn, actor, "system.backup", result["filename"], result)
        return result

    def overview(self):
        spaces = self.spaces()
        tickets = self.tickets()
        latest = self.db.one(
            "SELECT id FROM jobs WHERE status='completed' "
            "ORDER BY created_at DESC,id DESC LIMIT 1"
        )
        summary = self.completed_job(latest["id"])["result"]["summary"] if latest else None
        return {
            "spaces": len(spaces), "assets": sum(row["asset_count"] for row in spaces),
            "jobs": self.db.one("SELECT count(*) AS n FROM jobs")["n"],
            "open_tickets": sum(row["status"] != "closed" for row in tickets),
            "overdue_tickets": sum(row["overdue"] for row in tickets),
            "latest_summary": summary,
        }
