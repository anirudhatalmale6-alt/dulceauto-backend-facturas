"""
Entorno de Alembic.

La URL de la base de datos se toma de la configuracion de la aplicacion y no
del alembic.ini. Asi solo hay un sitio donde cambiarla el dia que se pase de
sqlite a MySQL o PostgreSQL, que es justo la garantia que se acordo.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings
from app.db import Base

# Importar los modelos registra las tablas en Base.metadata. Sin esta linea,
# autogenerate no ve nada y genera migraciones vacias.
from app import models  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", settings.sqlalchemy_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.sqlalchemy_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # sqlite no sabe hacer ALTER TABLE de casi nada. Con esto, Alembic
            # recrea la tabla y copia los datos. En MySQL o PostgreSQL no se usa
            # y no estorba, asi que se deja puesto para que las migraciones
            # funcionen igual en los tres motores.
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
