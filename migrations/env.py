"""Alembic environment configuration"""
import os
from alembic import context
from sqlalchemy import engine_from_config, pool

# Get config object
config = context.config

# Get database URL from environment
database_url = os.environ.get('SQLALCHEMY_DATABASE_URL')
if database_url:
    config.set_main_option('sqlalchemy.url', database_url)

def run_migrations_offline() -> None:
    """Run migrations in offline mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in online mode."""
    configuration = config.get_section(config.config_ini_section)
    database_url = os.environ.get('SQLALCHEMY_DATABASE_URL')
    if database_url:
        configuration["sqlalchemy.url"] = database_url
    
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=None
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
