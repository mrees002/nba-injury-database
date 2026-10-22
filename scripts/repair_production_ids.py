"""Restore automatic IDs in an existing lean PostgreSQL database, preserving data."""

from pathlib import Path

import psycopg

from app.config import get_settings


def main() -> None:
    sql = Path(__file__).with_suffix(".sql").read_text()
    url = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as connection:
        connection.execute(sql)
    print("Automatic IDs repaired for all five production tables; existing rows preserved.")


if __name__ == "__main__":
    main()
