import time
from urllib.parse import unquote, urlparse

import psycopg

from app.core.config import settings


def wait_for_db(max_attempts: int = 30, delay_seconds: float = 1.5) -> None:
    attempts = 0
    last_error = None
    parsed = urlparse(settings.database_url)
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    hostname = parsed.hostname or ""
    port = parsed.port or 5432
    database = (parsed.path or "").lstrip("/")
    if not username or not password or not hostname or not database:
        raise RuntimeError(
            "Database credentials missing in DATABASE_URL (username/password)."
        )
    print(
        f"Waiting for DB at {hostname}:{port}/{database} "
        f"as {username} (password set: {'yes' if password else 'no'})"
    )
    while attempts < max_attempts:
        try:
            with psycopg.connect(
                host=hostname,
                port=port,
                dbname=database,
                user=username,
                password=password,
            ) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
            return
        except Exception as exc:  # pragma: no cover - startup probe
            last_error = exc
            attempts += 1
            time.sleep(delay_seconds)
    raise RuntimeError(f"Database not ready after {max_attempts} attempts") from last_error


if __name__ == "__main__":
    wait_for_db()
