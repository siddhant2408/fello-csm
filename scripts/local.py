"""Local Docker-Postgres runner; secrets remain in process environment, never arguments."""

import os
import subprocess
import sys
from pathlib import Path

config = dict(
    line.split("=", 1)
    for line in Path(".env").read_text().splitlines()
    if "=" in line and not line.startswith("#")
)
env = os.environ | config
env["DATABASE_URL"] = f"postgresql://fello_app:{config['APP_DB_PASSWORD']}@127.0.0.1:55432/fello"
if sys.argv[1] == "bootstrap":
    env["ADMIN_DATABASE_URL"] = f"postgresql://postgres:{config['POSTGRES_PASSWORD']}@127.0.0.1:55432/fello"
    command = [sys.executable, "-m", "app.bootstrap"]
elif sys.argv[1] == "test":
    command = [sys.executable, "-m", "pytest", *sys.argv[2:]]
elif sys.argv[1] == "drain":
    command = [sys.executable, "-m", "app.cli", "drain"]
elif sys.argv[1] == "web":
    env["COOKIE_SECURE"] = "false"  # local HTTP only, not deployment default
    command = [sys.executable, "-m", "uvicorn", "app.web:app", "--host", "127.0.0.1", "--port", "8080"]
else:
    raise SystemExit("Use bootstrap, test, drain, web")
raise SystemExit(subprocess.call(command, env=env))
