"""Record exact tracked text source for CI evidence and delivery binding."""
from pathlib import Path
import hashlib
import json
import subprocess

root = Path(__file__).resolve().parents[1]
output = root / ".evidence"
output.mkdir(exist_ok=True)
tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
manifest = {}
for name in filter(None, tracked):
    path = root / name
    if path.is_file():
        manifest[name] = hashlib.sha256(path.read_bytes()).hexdigest()
(output / "source-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip()
(output / "commit.txt").write_text(commit + "\n", encoding="utf-8")
print(json.dumps({"commit": commit, "files": len(manifest)}))
