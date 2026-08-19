"""
Contrasenas y control de acceso.

Dos barreras independientes, tal y como pidio el cliente:

  1. Contrasena de panel: cuenta unica y compartida, da acceso al backend.
  2. Master Password: segunda contrasena, solo para Configuracion. Se pide
     aunque la sesion del panel ya este abierta, y se vuelve a pedir sola tras
     un rato de inactividad o al cerrar sesion.

Ninguna de las dos aparece jamas en el HTML ni en el JavaScript. Viajan en el
cuerpo de un POST y se comparan contra un hash guardado en la base de datos.

Sobre el algoritmo: se usa scrypt, que viene en la libreria estandar de Python.
Es un hash lento y con coste de memoria, disenado justo para esto. Se prefirio
a argon2 o bcrypt por una razon practica: no anade ninguna dependencia que
haya que compilar en el servidor del cliente, y para un panel interno la
diferencia de seguridad real entre ambos es nula.
"""
import hmac
import secrets
from datetime import datetime, timedelta
from hashlib import scrypt

from fastapi import Request
from sqlalchemy.orm import Session

from .config import settings
from .models import CRED_ADMIN, CRED_MASTER, Credential, utcnow

# Parametros de scrypt. n es el coste; subirlo endurece el hash y lo hace mas
# lento. 2**15 tarda del orden de 100 ms, que es imperceptible al entrar y
# carisimo para quien intente probar contrasenas por fuerza bruta.
_N = 2**15
_R = 8
_P = 1
_DKLEN = 64

# scrypt necesita 128 * n * r bytes de memoria, que con estos parametros son
# 32 MiB justos. OpenSSL trae un tope propio de 32 MiB y rechaza la llamada por
# quedarse a un pelo, con un error que no menciona scrypt por ningun lado
# ("memory limit exceeded"). Hay que subirlo a mano.
_MAXMEM = 128 * _N * _R * 2

SESSION_USER = "auth_user"
SESSION_LOGIN_AT = "auth_at"
SESSION_SEEN_AT = "auth_seen"
SESSION_MASTER_AT = "master_at"


# --- hashing -----------------------------------------------------------------


def hash_password(password: str) -> str:
    """Devuelve 'scrypt$n$r$p$salt$hash'. La sal es distinta en cada llamada."""
    salt = secrets.token_bytes(16)
    dk = scrypt(
        password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN, maxmem=_MAXMEM
    )
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Comparacion en tiempo constante, para no filtrar informacion por el
    tiempo que tarda en responder."""
    try:
        algo, n, r, p, salt_hex, hash_hex = stored.split("$")
        if algo != "scrypt":
            return False
        dk = scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(hash_hex) // 2,
            # El coste se lee del propio hash, no de las constantes de arriba:
            # asi los hashes creados con parametros antiguos siguen validando
            # despues de subir el coste.
            maxmem=128 * int(n) * int(r) * 2,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


# --- credenciales ------------------------------------------------------------


def get_credential(db: Session, name: str) -> Credential | None:
    return db.get(Credential, name)


def check_admin(db: Session, username: str, password: str) -> bool:
    cred = get_credential(db, CRED_ADMIN)
    if cred is None:
        return False
    if (cred.username or "").strip().lower() != (username or "").strip().lower():
        return False
    return verify_password(password, cred.password_hash)


def check_master(db: Session, password: str) -> bool:
    cred = get_credential(db, CRED_MASTER)
    return bool(cred) and verify_password(password, cred.password_hash)


def set_password(db: Session, name: str, password: str, username: str | None = None) -> None:
    cred = get_credential(db, name)
    if cred is None:
        cred = Credential(name=name, password_hash=hash_password(password))
        db.add(cred)
    else:
        cred.password_hash = hash_password(password)
    if username is not None:
        cred.username = username
    cred.must_change = False
    cred.updated_at = utcnow()
    db.commit()


# --- sesion del panel --------------------------------------------------------


def login_session(request: Request, username: str) -> None:
    now = utcnow().isoformat()
    request.session.clear()
    request.session[SESSION_USER] = username
    request.session[SESSION_LOGIN_AT] = now
    request.session[SESSION_SEEN_AT] = now


def logout_session(request: Request) -> None:
    request.session.clear()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def current_user(request: Request) -> str | None:
    """Usuario de la sesion, o None si no hay sesion o ya caduco.

    Cada peticion valida refresca la marca de actividad, de modo que la sesion
    caduca por inactividad y no por tiempo absoluto.
    """
    user = request.session.get(SESSION_USER)
    if not user:
        return None
    seen = _parse(request.session.get(SESSION_SEEN_AT))
    if seen is None or utcnow() - seen > timedelta(minutes=settings.session_minutes):
        request.session.clear()
        return None
    request.session[SESSION_SEEN_AT] = utcnow().isoformat()
    return user


# --- puerta de Configuracion -------------------------------------------------


def unlock_master(request: Request) -> None:
    request.session[SESSION_MASTER_AT] = utcnow().isoformat()


def lock_master(request: Request) -> None:
    request.session.pop(SESSION_MASTER_AT, None)


def master_unlocked(request: Request) -> bool:
    """Configuracion esta desbloqueada solo si se introdujo la Master Password
    hace menos de master_session_minutes. Pasado ese tiempo se vuelve a cerrar
    sola, aunque la sesion del panel siga abierta."""
    at = _parse(request.session.get(SESSION_MASTER_AT))
    if at is None:
        return False
    if utcnow() - at > timedelta(minutes=settings.master_session_minutes):
        lock_master(request)
        return False
    return True


def touch_master(request: Request) -> None:
    """Renueva la ventana de Configuracion mientras se esta trabajando en ella."""
    if master_unlocked(request):
        request.session[SESSION_MASTER_AT] = utcnow().isoformat()
