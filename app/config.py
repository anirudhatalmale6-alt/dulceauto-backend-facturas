"""
Configuracion de la aplicacion.

Todo lo sensible se lee del entorno, nunca del codigo. En desarrollo se toma
del archivo .env; en el servidor, de las variables de entorno del contenedor.
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "DulceAuto · Facturas Premium"
    app_version: str = "V1 · Fase D"
    debug: bool = False

    # Clave de firma de la cookie de sesion. En produccion es obligatorio
    # cambiarla: si se filtra, se pueden falsificar sesiones.
    secret_key: str = "cambiar-esta-clave-en-produccion"

    # Rutas de datos. Se mantienen fuera de app/ para que el contenedor pueda
    # montarlas como volumen y sobrevivan a un redespliegue.
    data_dir: Path = PROJECT_DIR / "data"

    # sqlite por defecto. Al ir siempre a traves de SQLAlchemy, migrar a
    # MySQL o PostgreSQL es cambiar esta cadena y correr las migraciones.
    database_url: str = ""

    # Minutos de inactividad tras los que Configuracion se vuelve a bloquear
    # sola, aunque la sesion del panel siga abierta.
    master_session_minutes: int = 15

    # Minutos de inactividad tras los que se cierra la sesion del panel.
    session_minutes: int = 480

    # Credenciales iniciales. Solo se usan para sembrar la base la primera
    # vez; despues viven hasheadas en la tabla credential y se cambian desde
    # el panel. Nunca se comparan contra estos valores en caliente.
    initial_admin_user: str = "admin"
    initial_admin_password: str = "DulceAuto2026"
    initial_master_password: str = "Master2026"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def snapshots_dir(self) -> Path:
        return self.data_dir / "snapshots"

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'dulceauto.db').as_posix()}"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    for d in (s.data_dir, s.uploads_dir, s.snapshots_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s


settings = get_settings()
