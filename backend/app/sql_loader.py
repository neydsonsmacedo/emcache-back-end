from functools import lru_cache
from pathlib import Path

SQL_DIR = (Path(__file__).resolve().parent / "sql").resolve()


@lru_cache(maxsize=128)
def load_sql(relative_path: str) -> str:
    """Load and cache a static SQL file from backend/app/sql/ safely."""
    target = (SQL_DIR / relative_path).resolve()
    if not target.is_relative_to(SQL_DIR) or target.suffix != ".sql":
        raise ValueError(f"Invalid SQL path: {relative_path}")
    if not target.is_file():
        raise FileNotFoundError(f"SQL file not found: {relative_path}")
    return target.read_text(encoding="utf-8")
