"""
Capa de acceso a datos.

Todo pasa por SQLAlchemy a proposito. No hay una sola linea de SQL escrita a
mano ni ningun tipo propio de SQLite, de modo que el dia que haga falta pasar
a MySQL o PostgreSQL basta con cambiar DATABASE_URL y correr las migraciones.
"""
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

_is_sqlite = settings.sqlalchemy_url.startswith("sqlite")

engine = create_engine(
    settings.sqlalchemy_url,
    # check_same_thread solo aplica a sqlite; en otros motores no existe.
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=True,
    future=True,
)


if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        """sqlite no aplica claves foraneas si no se le pide expresamente.

        Sin esto, un borrado dejaria filas huerfanas y el comportamiento no
        coincidiria con el de MySQL o PostgreSQL, que es justo lo que hay que
        evitar si la migracion futura tiene que ser indolora.
        """
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
