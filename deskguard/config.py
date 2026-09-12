"""Local storage configuration; no cloud credentials are accepted."""
from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class Settings:
    home: Path
    session_seconds: int = 28800
    max_body_bytes: int = 2_000_000

    @property
    def database(self) -> Path:
        return self.home / "deskguard.sqlite3"

    @property
    def backups(self) -> Path:
        return self.home / "backups"

    def prepare(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.backups.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            self.home.chmod(0o700)
            self.backups.chmod(0o700)

    @classmethod
    def from_env(cls):
        default = Path.home() / ".deskguard"
        return cls(Path(os.environ.get("DESKGUARD_HOME", default)).resolve())
