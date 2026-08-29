"""
Registro de actividad.

Basico a proposito, que es lo que pidio el cliente: quien, que, sobre que
factura y cuando. Sin auditoria avanzada ni diffs campo a campo.
"""
from fastapi import Request
from sqlalchemy.orm import Session

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

LABELS = {
    LOGIN: "Inicio de sesión",
    LOGOUT: "Cierre de sesión",
    LOGIN_FAILED: "Intento de acceso fallido",
    MASTER_UNLOCK: "Configuración desbloqueada",
    MASTER_FAILED: "Master Password incorrecta",
    MASTER_LOCK: "Configuración bloqueada",
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
