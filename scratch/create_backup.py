import os
import subprocess
from pathlib import Path

backup_dir = Path("c:/Projects/storeshare/backups")
backup_dir.mkdir(parents=True, exist_ok=True)
backup_file = backup_dir / "storeshare_pre_cleanup_backup_20261023.sql"

pg_dump = r"C:\Program Files\PostgreSQL\18\bin\pg_dump.exe"
env = os.environ.copy()
env["PGPASSWORD"] = "storeshare123"

cmd = [
    pg_dump,
    "-h", "localhost",
    "-p", "5432",
    "-U", "storeshare_app",
    "-d", "storeshare",
    "-F", "p",
    "-f", str(backup_file),
]

print("Running pg_dump...")
result = subprocess.run(cmd, env=env, capture_output=True, text=True)

if result.returncode != 0:
    print("pg_dump failed!")
    print("STDOUT:", result.stdout)
    print("STDERR:", result.stderr)
else:
    print("pg_dump succeeded!")
    size = backup_file.stat().st_size
    print(f"Backup file: {backup_file}")
    print(f"File size: {size} bytes")
    # Read first 15 lines of backup to verify
    with open(backup_file, "r", encoding="utf-8") as f:
        head = [next(f) for _ in range(15)]
    print("\nBackup Header:\n" + "".join(head))
