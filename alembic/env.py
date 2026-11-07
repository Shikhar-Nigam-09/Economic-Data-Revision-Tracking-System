from logging.config import fileConfig
from typing import Any

from sqlalchemy import create_engine, pool

import econ_monitor.db.models  # noqa: F401 - ensures all models are registered on metadata
from alembic import context
from econ_monitor.core.config import get_settings
from econ_monitor.core.errors import ConfigurationError
from econ_monitor.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    """Return database URL from Alembic config option or environment settings."""
    url = config.get_main_option("sqlalchemy.url")
    if url and url.strip():
        return url.strip()

    settings = get_settings()
    try:
        return settings.require_database_url()
    except ConfigurationError:
        # Fallback for offline documentation or template generation
        if context.is_offline_mode():
            return "postgresql+psycopg://postgres:postgres@localhost:5432/econ_monitor"
        raise


def include_object(object: Any, name: str, type_: str, reflected: bool, compare_to: Any) -> bool:
    """Filter out custom views from autogenerate comparison."""
    if type_ in ("table", "view") and name == "latest_observation_values":
        return False
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # If connection was passed programmatically (e.g. in test fixtures), use it directly
    connectable = config.attributes.get("connection", None)

    if connectable is None:
        db_url = get_url()
        connectable = create_engine(db_url, poolclass=pool.NullPool)

        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                include_object=include_object,
            )

            with context.begin_transaction():
                context.run_migrations()
    else:
        context.configure(
            connection=connectable,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
