"""Regenerates the binary fixtures in this folder: ``python tests/fixtures/make_fixtures.py``."""

from pathlib import Path

import duckdb

HERE = Path(__file__).parent


def main() -> None:
    con = duckdb.connect()
    con.execute(
        """
        CREATE TABLE orders (
            id BIGINT NOT NULL,
            customer VARCHAR,
            amount DECIMAL(10, 2) NOT NULL,
            created DATE,
            paid BOOLEAN
        )
        """
    )
    con.execute("INSERT INTO orders VALUES (1, 'ann', 10.50, DATE '2024-01-01', true)")
    con.execute(f"COPY orders TO '{(HERE / 'orders.parquet').as_posix()}' (FORMAT PARQUET)")

    part = HERE / "orders_dir"
    part.mkdir(exist_ok=True)
    for i in (1, 2):
        con.execute(f"COPY orders TO '{(part / f'part{i}.parquet').as_posix()}' (FORMAT PARQUET)")


if __name__ == "__main__":
    main()
