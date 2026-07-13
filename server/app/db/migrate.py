"""Add columns that models.py declares but an existing SQLite file lacks.

There is no Alembic here, and for a single-workstation SQLite app there does not need
to be: SQLite can ADD COLUMN in place, and create_all() already handles new tables. This
closes the only remaining gap — a new column on an existing table — so a schema change
no longer means wiping the operator's database and re-reviewing every drawing.

It only ever ADDs. It will not drop, rename or retype anything; if that is ever needed,
that is the day to introduce a real migration tool.
"""

from sqlalchemy import Engine, inspect, text
from sqlalchemy.orm import DeclarativeBase

# SQLite cannot add a column with a non-constant default, so give it a literal.
_SQL_DEFAULT = {
    "BOOLEAN": "0",
    "INTEGER": "0",
    "FLOAT": "0",
}


def add_missing_columns(engine: Engine, base: type[DeclarativeBase]) -> list[str]:
    """Returns the columns it added, for logging."""
    added: list[str] = []
    inspector = inspect(engine)

    with engine.begin() as conn:
        for table in base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue  # create_all() will make it
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                type_sql = column.type.compile(engine.dialect)
                clause = f"{column.name} {type_sql}"
                if not column.nullable:
                    default = _SQL_DEFAULT.get(type_sql.upper(), "''")
                    clause += f" NOT NULL DEFAULT {default}"
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {clause}"))
                added.append(f"{table.name}.{column.name}")

    return added
