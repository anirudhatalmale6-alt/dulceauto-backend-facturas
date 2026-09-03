"""
Registro de actividad.

Basico a proposito, que es lo que pidio el cliente: quien, que, sobre que
factura y cuando. Sin auditoria avanzada ni diffs campo a campo.
"""
import csv
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import settings
from .models import ActivityLog

# Acciones que se registran. Se declaran como constantes para que la vista de
# Actividad pueda traducirlas sin depender de cadenas sueltas repartidas por el
# codigo.
LOGIN = "login"
LOGOUT = "logout"
LOGIN_FAILED = "login_failed"
MASTER_UNLOCK = "master_unlock"
MASTER_FAILED = "master_failed"
MASTER_LOCK = "master_lock"
INVOICE_CREATED = "invoice_created"
INVOICE_UPDATED = "invoice_updated"
INVOICE_DUPLICATED = "invoice_duplicated"
INVOICE_DRAFT_SAVED = "invoice_draft_saved"
PDF_GENERATED = "pdf_generated"
SETTINGS_UPDATED = "settings_updated"
PASSWORD_CHANGED = "password_changed"

# Call Center. El acceso del Operador se registra igual que el del Admin: la
# cuenta es compartida, asi que la Actividad es lo unico que permite reconstruir
# quien estuvo mirando que reserva y cuando.
OPERATOR_LOGIN = "operator_login"
OPERATOR_LOGOUT = "operator_logout"
OPERATOR_LOGIN_FAILED = "operator_login_failed"
OPERATOR_LOOKUP = "operator_lookup"
OPERATOR_NOTE = "operator_note"
OPERATOR_DENIED = "operator_denied"

# Administracion de la guia y de las notas.
FAQ_CREATED = "faq_created"
FAQ_UPDATED = "faq_updated"
FAQ_DELETED = "faq_deleted"
NOTE_REVIEWED = "note_reviewed"

# Retirada de un documento del historico. No se hace desde el panel: no hay
# boton de borrar un snapshot, y es a proposito. Se registra igualmente para que
# quede explicado por que un historico tiene un hueco.
SNAPSHOT_REMOVED = "snapshot_removed"

# Limpieza del propio historial. Se registra DESPUES del borrado, para que la
# entrada sobreviva a la limpieza que la provoca: si se escribiera antes, el
# DELETE se la llevaria por delante y la Actividad quedaria vacia sin ninguna
# constancia de quien la vacio.
ACTIVITY_CLEARED = "activity_cleared"

LABELS = {
    LOGIN: "Inicio de sesión",
    LOGOUT: "Cierre de sesión",
    LOGIN_FAILED: "Intento de acceso fallido",
    # Desde el Hito A la Master Password abre Configuracion Y Actividad, asi que
    # la etiqueta ya no puede nombrar solo a una. El detalle de cada entrada
    # dice desde cual de las dos se abrio.
    MASTER_UNLOCK: "Área protegida desbloqueada",
    MASTER_FAILED: "Master Password incorrecta",
    MASTER_LOCK: "Área protegida bloqueada",
    INVOICE_CREATED: "Factura creada",
    INVOICE_UPDATED: "Factura editada",
    INVOICE_DUPLICATED: "Factura duplicada",
    INVOICE_DRAFT_SAVED: "Borrador guardado",
    PDF_GENERATED: "PDF generado",
    SETTINGS_UPDATED: "Configuración modificada",
    PASSWORD_CHANGED: "Contraseña cambiada",
    OPERATOR_LOGIN: "Acceso de Operador",
    OPERATOR_LOGOUT: "Salida de Operador",
    OPERATOR_LOGIN_FAILED: "Acceso de Operador fallido",
    OPERATOR_LOOKUP: "Consulta de reserva (Operador)",
    OPERATOR_NOTE: "Nota de Operador",
    OPERATOR_DENIED: "Acceso a Administración bloqueado",
    FAQ_CREATED: "Entrada añadida a la guía",
    FAQ_UPDATED: "Entrada de la guía modificada",
    FAQ_DELETED: "Entrada de la guía eliminada",
    NOTE_REVIEWED: "Nota revisada",
    SNAPSHOT_REMOVED: "Documento retirado del histórico",
    ACTIVITY_CLEARED: "Historial de actividad limpiado",
}


def client_ip(request: Request | None) -> str | None:
    """IP real del visitante. Detras de un proxy inverso, request.client es la
    del proxy, asi que se mira antes la cabecera que este pone."""
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def log(
    db: Session,
    action: str,
    *,
    actor: str = "Admin",
    entity_type: str | None = None,
    entity_id: int | None = None,
    folio: str | None = None,
    detail: str | None = None,
    request: Request | None = None,
    commit: bool = True,
) -> ActivityLog:
    entry = ActivityLog(
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        folio=folio,
        detail=detail,
        ip=client_ip(request),
    )
    db.add(entry)
    if commit:
        db.commit()
    return entry


# --- limpieza del historial --------------------------------------------------
#
# El panel gana aqui su primera y unica forma de borrar Actividad. Se hace en
# dos pasos deliberados -copia primero, borrado despues- y el borrado solo se
# ejecuta si la copia quedo completa y verificada.

CABECERA_CSV = (
    "id",
    "fecha_utc",
    "usuario",
    "accion",
    "accion_legible",
    "tipo_entidad",
    "id_entidad",
    "folio",
    "detalle",
    "ip",
)

# Caracteres con los que Excel y LibreOffice interpretan la celda como formula.
# Un detalle que empiece por "+52 55..." o por "=" se ejecutaria al abrir el
# archivo, asi que se le antepone un apostrofo: las hojas de calculo lo comen y
# muestran el texto tal cual.
PELIGROSOS = ("=", "+", "-", "@", "\t", "\r")


def _celda(valor) -> str:
    """Una celda que una hoja de calculo no pueda interpretar como formula."""
    if valor is None:
        return ""
    texto = str(valor)
    if texto[:1] in PELIGROSOS:
        return "'" + texto
    return texto


def _ruta_export(momento: datetime) -> Path:
    """Nombre unico para la copia. El sufijo solo aparece si ya existe una copia
    del mismo segundo; sin el, la segunda limpieza pisaria la primera."""
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    base = momento.strftime("actividad-%Y%m%d-%H%M%S")
    ruta = settings.exports_dir / f"{base}.csv"
    n = 2
    while ruta.exists():
        ruta = settings.exports_dir / f"{base}-{n}.csv"
        n += 1
    return ruta


def exportar_csv(db: Session, momento: datetime | None = None) -> tuple[Path, int]:
    """Vuelca TODO el historial a un CSV en el servidor y devuelve la ruta y el
    numero de filas escritas. En orden cronologico, que es como lo va a leer una
    persona, no en el orden invertido de la pantalla.

    El archivo lleva BOM: sin el, Excel abre el CSV en su codificacion local y
    'Configuración' se ve como 'ConfiguraciÃ³n'.
    """
    momento = momento or datetime.now(timezone.utc).replace(tzinfo=None)
    ruta = _ruta_export(momento)
    filas = 0
    with ruta.open("w", encoding="utf-8-sig", newline="") as fh:
        # QUOTE_ALL y el modulo csv, no un join a mano: un detalle con una coma,
        # una comilla o un salto de linea desplazaria las columnas de esa fila y
        # el resto del archivo se leeria corrido.
        escritor = csv.writer(fh, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
        escritor.writerow(CABECERA_CSV)
        entradas = db.execute(
            select(ActivityLog).order_by(ActivityLog.id)
        ).scalars()
        for e in entradas:
            escritor.writerow(
                [
                    e.id,
                    e.created_at.strftime("%Y-%m-%d %H:%M:%S") if e.created_at else "",
                    _celda(e.actor),
                    _celda(e.action),
                    _celda(LABELS.get(e.action, e.action)),
                    _celda(e.entity_type),
                    e.entity_id if e.entity_id is not None else "",
                    _celda(e.folio),
                    _celda(e.detail),
                    _celda(e.ip),
                ]
            )
            filas += 1
    return ruta, filas


class CopiaIncompleta(RuntimeError):
    """La tabla cambio entre la copia y el borrado. No se borra nada."""


def limpiar_historial(
    db: Session, *, request: Request | None = None, actor: str = "Admin"
) -> tuple[Path, int]:
    """Copia el historial, comprueba que la copia esta completa y solo entonces
    vacia activity_log. No toca ninguna otra tabla ni ningun archivo: ni
    facturas, ni notas, ni snapshots, ni fotos, ni folios, ni Configuracion.

    Devuelve la ruta de la copia y cuantas entradas habia.
    """
    ruta, filas = exportar_csv(db)

    # Segunda cuenta despues de escribir la copia. Si alguien registro algo
    # mientras se exportaba, esa entrada no esta en el archivo: se aborta en vez
    # de borrarla sin copia.
    ahora = db.execute(select(func.count(ActivityLog.id))).scalar_one()
    if ahora != filas:
        ruta.unlink(missing_ok=True)
        raise CopiaIncompleta(
            f"La copia recogio {filas} entradas y la tabla tiene {ahora}."
        )

    db.execute(delete(ActivityLog))
    db.flush()

    # Y ahora, con la tabla ya vacia, la unica entrada que queda: quien, cuando
    # y cuantas habia. Escrita despues del DELETE a proposito.
    log(
        db,
        ACTIVITY_CLEARED,
        actor=actor,
        detail=f"{filas} entradas eliminadas · copia en {ruta.name}",
        request=request,
        commit=True,
    )
    return ruta, filas
