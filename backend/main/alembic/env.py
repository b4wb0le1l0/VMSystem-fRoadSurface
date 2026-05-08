from __future__ import annotations
from logging.config import fileConfig
import os

from sqlalchemy import engine_from_config, pool
from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

alembic_url = None
for x in context.get_x_argument(as_dictionary=True).items():
    if x[0] == "alembic_database_url":
        alembic_url = x[1]
        break

if not alembic_url:
    alembic_url = os.environ.get("ALEMBIC_DATABASE_URL")

if not alembic_url:
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url.startswith("postgresql://"):
        alembic_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

if not alembic_url:
    raise RuntimeError("No database URL provided for Alembic")

config.set_main_option("sqlalchemy.url", alembic_url)

target_metadata = None

def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
