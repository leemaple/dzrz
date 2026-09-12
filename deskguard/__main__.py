"""Initialization and loopback server entry points."""
import argparse
import getpass
import json
import os
import sys
from . import NAME, VERSION
from .config import Settings
from .database import Database
from .security import Auth


def main():
    parser = argparse.ArgumentParser(description=NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("init", help="初始化本地管理员")
    setup.add_argument("--username", default="admin")
    commands.add_parser("doctor", help="显示本地运行环境状态")
    serve = commands.add_parser("serve", help="只在127.0.0.1启动服务")
    serve.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    settings = Settings.from_env()
    settings.prepare()
    db = Database(settings.database)
    db.initialize()
    auth = Auth(db)
    if args.command == "init":
        password = os.environ.get("DESKGUARD_ADMIN_PASSWORD")
        if not password:
            password = getpass.getpass("设置管理员口令（至少12位）：")
            if password != getpass.getpass("再次输入口令："):
                parser.error("两次输入不一致")
        try:
            auth.setup(args.username, password)
        except ValueError as exc:
            parser.error(str(exc))
        print("管理员初始化成功；未打印或保存明文口令。")
    elif args.command == "doctor":
        print(json.dumps({
            "software": NAME, "version": VERSION, "python": sys.version.split()[0],
            "initialized": auth.is_initialized(), "database": str(db.path),
            "engine": "deskguard-rules-v1", "audit": db.verify_audit(),
            "network_scope": "127.0.0.1 only",
        }, ensure_ascii=False, indent=2))
    else:
        if not 1024 <= args.port <= 65535:
            parser.error("端口须为1024至65535")
        if not auth.is_initialized():
            parser.error("请先运行 python -m deskguard init")
        import uvicorn
        from .api import create_app
        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port,
                    workers=1, access_log=False)


if __name__ == "__main__":
    main()
