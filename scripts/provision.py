"""Generate local secrets without printing them. Refuse overwriting existing credentials."""

import os
import secrets
from pathlib import Path

path = Path(".env")
content = "\n".join(
    f"{key}={secrets.token_hex(24)}"
    for key in ["POSTGRES_PASSWORD", "APP_DB_PASSWORD", "SESSION_SECRET"]
)
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as stream:
    stream.write(
        content + "\nOPENROUTER_API_KEY=\nSIMULATION_AS_OF=2026-06-01T12:00:00+00:00\n"
    )
print("Created .env with mode 0600; credentials were not printed.")
