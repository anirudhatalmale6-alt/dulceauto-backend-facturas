"""
Aplicacion FastAPI del backend de facturas.

Fase A: base del proyecto, acceso, Master Password, modelo de datos y el panel
completo con sus seis vistas y sus tres modos visuales.

Fase B: crear, editar, guardar borrador, buscar, duplicar y agrupacion por VIN.

La vista previa real y el PDF llegan en las fases C y D. Donde todavia no estan
cableadas, la pantalla lo dice con una etiqueta en lugar de ofrecer un boton que
no hace nada.
"""
from fastapi import Depends, FastAPI, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import activity as act
from . import invoices as inv_service
from .config import BASE_DIR, settings
from .db import Base, engine, get_db
from .fields import EDITABLE_FIELDS
from .locales import MARKETS, format_amount, get_market
from .models import (
    CRED_ADMIN,
    CRED_MASTER,
    STATUS_DRAFT,
    STATUS_GENERATED,
    STATUS_PENDING,
    STATUS_SENT,
    ActivityLog,
    Credential,
    Invoice,
    Setting,
    utcnow,
)
from .security import (
    check_admin,
    check_master,
    current_user,
    lock_master,
    login_session,
    logout_session,
    master_unlocked,
    set_password,
    touch_master,
    unlock_master,
)
from .seed import run as run_seed

app = FastAPI(title=settings.app_name, docs_url=None, redoc_url=None)

# La cookie de sesion va firmada. https_only se deja en manos del entorno: en
# el VPS con certificado hay que ponerlo a true, en desarrollo local rompe.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="da_session",
    same_site="lax",
    https_only=False,
    max_age=settings.session_minutes * 60,
)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

NAV_ITEMS = [
    {"key": "dashboard", "label": "Dashboard", "icon": "⌂", "url": "/"},
    {"key": "invoices", "label": "Facturas", "icon": "▤", "url": "/facturas"},
    {"key": "editor", "label": "Crear / Editar", "icon": "✎", "url": "/facturas/nueva"},
    {"key": "templates", "label": "Plantillas", "icon": "◫", "url": "/plantillas"},
    {"key": "activity", "label": "Actividad", "icon": "◷", "url": "/actividad"},
    {"key": "settings", "label": "Configuración", "icon": "⚙", "url": "/configuracion"},
]

STATUS_LABELS = {
    STATUS_DRAFT: ("Borrador", "muted"),
    STATUS_PENDING: ("Pago pendiente", "pending"),
    STATUS_GENERATED: ("PDF generado", "generated"),
    STATUS_SENT: ("Enviada", "generated"),
    "cancelled": ("Cancelada", "muted"),
}


@app.on_event("startup")
def on_startup() -> None:
    """Crea el esquema si falta y siembra lo minimo.

    En el servidor las tablas las crea Alembic; create_all esta aqui para que
    levantar el proyecto en local sea un solo comando. Como create_all no toca
    lo que ya existe, las dos vias conviven sin pisarse.
    """
    Base.metadata.create_all(bind=engine)
    from .db import SessionLocal

    with SessionLocal() as db:
        run_seed(db)


# --- utilidades de plantilla -------------------------------------------------


def theme_from(request: Request) -> str:
    theme = request.cookies.get("da_theme", "light")
    return theme if theme in ("light", "soft", "night") else "light"


def render(
    request: Request,
    template: str,
    db: Session,
    *,
    active_view: str,
    page_title: str,
    page_sub: str | None = None,
    **context,
) -> HTMLResponse:
    theme = theme_from(request)
    admin = db.get(Credential, CRED_ADMIN)
    master = db.get(Credential, CRED_MASTER)
    base = {
        "nav_items": NAV_ITEMS,
        "active_view": active_view,
        "page_title": page_title,
        "page_sub": page_sub,
        "app_version": settings.app_version,
        "theme": theme,
        "theme_class": {"light": "", "soft": "theme-soft", "night": "theme-night"}[theme],
        "user": current_user(request),
        "must_change_password": bool(
            (admin and admin.must_change) or (master and master.must_change)
        ),
        "flashes": pop_flashes(request),
        "status_labels": STATUS_LABELS,
        "markets": MARKETS,
        "money": format_amount,
    }
    base.update(context)
    # Starlette pide la peticion como primer argumento; la firma antigua
    # (nombre, contexto) interpreta el nombre como la peticion y revienta.
    return templates.TemplateResponse(request, template, base)


def flash(request: Request, text: str, level: str = "ok") -> None:
    request.session.setdefault("_flashes", []).append({"text": text, "level": level})


def pop_flashes(request: Request) -> list[dict]:
    return request.session.pop("_flashes", [])


def require_login(request: Request) -> str | RedirectResponse:
    user = current_user(request)
    if not user:
        return RedirectResponse("/acceso", status_code=status.HTTP_303_SEE_OTHER)
    return user


# --- acceso ------------------------------------------------------------------


@app.get("/acceso", response_class=HTMLResponse)
def login_form(request: Request):
    if current_user(request):
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    theme = theme_from(request)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "app_version": settings.app_version,
            "theme_class": {"light": "", "soft": "theme-soft", "night": "theme-night"}[theme],
            "error": request.session.pop("_login_error", None),
        },
    )


@app.post("/acceso")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if check_admin(db, username, password):
        login_session(request, username)
        act.log(db, act.LOGIN, request=request, detail=f"usuario {username}")
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    # No se distingue entre usuario inexistente y contrasena incorrecta: decir
    # cual de las dos falla le regala media respuesta a quien este probando.
    act.log(db, act.LOGIN_FAILED, request=request, detail=f"usuario {username}")
    request.session["_login_error"] = "Usuario o contraseña incorrectos."
    return RedirectResponse("/acceso", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/salir", name="logout")
def logout(request: Request, db: Session = Depends(get_db)):
    if current_user(request):
        act.log(db, act.LOGOUT, request=request)
    # Al cerrar sesion se cae tambien el desbloqueo de Configuracion, porque
    # logout_session vacia la sesion entera.
    logout_session(request)
    return RedirectResponse("/acceso", status_code=status.HTTP_303_SEE_OTHER)


# --- dashboard ---------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user

    total = db.execute(select(func.count(Invoice.id))).scalar_one()
    por_estado = dict(
        db.execute(select(Invoice.status, func.count(Invoice.id)).group_by(Invoice.status)).all()
    )
    por_mercado = dict(
        db.execute(select(Invoice.locale, func.count(Invoice.id)).group_by(Invoice.locale)).all()
    )
    # Vehiculos con mas de una factura: son los que tienen varios interesados.
    vin_counts = db.execute(
        select(Invoice.vehicle_vin, func.count(Invoice.id))
        .where(Invoice.vehicle_vin.is_not(None))
        .group_by(Invoice.vehicle_vin)
    ).all()
    compartidos = sum(1 for _, n in vin_counts if n > 1)

    recientes = db.execute(
        select(Invoice).order_by(Invoice.updated_at.desc()).limit(5)
    ).scalars().all()
    ultimas_acciones = db.execute(
        select(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(6)
    ).scalars().all()

    return render(
        request,
        "dashboard.html",
        db,
        active_view="dashboard",
        page_title="Dashboard",
        total=total,
        por_estado=por_estado,
        por_mercado=por_mercado,
        vehiculos=len(vin_counts),
        compartidos=compartidos,
        recientes=recientes,
        acciones=ultimas_acciones,
        action_labels=act.LABELS,
    )


# --- facturas ----------------------------------------------------------------


@app.get("/facturas", response_class=HTMLResponse)
def invoices(request: Request, q: str = "", db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user

    stmt = select(Invoice).order_by(Invoice.updated_at.desc())
    termino = (q or "").strip()
    if termino:
        # Busqueda por folio, cliente o vehiculo, que son los tres campos que
        # pidio el cliente. ilike para que no dependa de mayusculas.
        patron = f"%{termino}%"
        stmt = stmt.where(
            Invoice.folio.ilike(patron)
            | Invoice.customer_name.ilike(patron)
            | Invoice.vehicle_title.ilike(patron)
            | Invoice.vehicle_vin.ilike(patron)
        )
    filas = db.execute(stmt).scalars().all()

    # Cuantas facturas comparten VIN, para la columna de interesados.
    interesados = dict(
        db.execute(
            select(Invoice.vehicle_vin, func.count(Invoice.id))
            .where(Invoice.vehicle_vin.is_not(None))
            .group_by(Invoice.vehicle_vin)
        ).all()
    )

    return render(
        request,
        "invoices.html",
        db,
        active_view="invoices",
        page_title="Facturas",
        page_sub="Consulta la factura enviada, edita o duplica una pre-factura.",
        invoices=filas,
        q=termino,
        interesados=interesados,
    )


def editor_page(
    request: Request,
    db: Session,
    invoice: Invoice | None,
    *,
    errors: list[str] | None = None,
    form=None,
) -> HTMLResponse:
    """Pinta el editor.

    Cuando la validacion falla se le pasa el formulario tal y como lo envio el
    operador: reescribir la pantalla con los datos de la base le borraria lo que
    acababa de teclear, que es la peor manera de avisar de un error.
    """
    historial = inv_service.vin_history(
        db, invoice.vehicle_vin if invoice else None, invoice.id if invoice else None
    )
    return render(
        request,
        "editor.html",
        db,
        active_view="editor",
        page_title="Crear / Editar",
        page_sub=(
            f"Factura {invoice.folio} · {get_market(invoice.locale).label}"
            if invoice
            else "Una sola pantalla operativa para las 3 plantillas aprobadas."
        ),
        invoice=invoice,
        editable=EDITABLE_FIELDS,
        errors=errors or [],
        form=form,
        historial=historial,
        comprometidas=[i for i in historial if i.status in inv_service.COMMITTED_STATUSES],
    )


@app.get("/facturas/nueva", response_class=HTMLResponse)
def invoice_new(request: Request, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    return editor_page(request, db, None)


@app.post("/facturas/nueva")
async def invoice_create(request: Request, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user

    form = await request.form()
    invoice, errores = inv_service.create(db, form)
    if errores:
        # Rollback para que el contador de folios no se gaste con un intento
        # fallido: si no, cada error dejaria un hueco en la numeracion.
        db.rollback()
        return editor_page(request, db, None, errors=errores, form=form)

    db.commit()
    accion = act.INVOICE_DRAFT_SAVED if invoice.status == STATUS_DRAFT else act.INVOICE_CREATED
    act.log(db, accion, request=request, entity_type="invoice", entity_id=invoice.id,
            folio=invoice.folio, detail=get_market(invoice.locale).label)
    flash(request, f"Factura {invoice.folio} creada.", "ok")
    return RedirectResponse(
        f"/facturas/{invoice.id}/editar", status_code=status.HTTP_303_SEE_OTHER
    )


@app.get("/facturas/{invoice_id}/editar", response_class=HTMLResponse)
def invoice_edit(request: Request, invoice_id: int, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        flash(request, "Esa factura ya no existe.", "error")
        return RedirectResponse("/facturas", status_code=status.HTTP_303_SEE_OTHER)
    return editor_page(request, db, invoice)


@app.post("/facturas/{invoice_id}/editar")
async def invoice_update(request: Request, invoice_id: int, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user

    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        flash(request, "Esa factura ya no existe.", "error")
        return RedirectResponse("/facturas", status_code=status.HTTP_303_SEE_OTHER)

    form = await request.form()
    estado_previo = invoice.status
    mercado_previo = invoice.locale
    inv_service.apply_form(invoice, form)
    # Cambiar de mercado cambia la moneda y la cuenta: la CLABE mexicana no vale
    # para una factura argentina, asi que se vuelven a heredar los datos.
    if invoice.locale != mercado_previo:
        inv_service.inherit_settings(db, invoice)

    errores = inv_service.validate(invoice)
    if errores:
        db.rollback()
        return editor_page(request, db, db.get(Invoice, invoice_id), errors=errores, form=form)

    db.commit()
    accion = (
        act.INVOICE_DRAFT_SAVED
        if invoice.status == STATUS_DRAFT and estado_previo == STATUS_DRAFT
        else act.INVOICE_UPDATED
    )
    detalle = None
    if invoice.status != estado_previo:
        detalle = f"{STATUS_LABELS.get(estado_previo, (estado_previo,))[0]} → {STATUS_LABELS.get(invoice.status, (invoice.status,))[0]}"
    act.log(db, accion, request=request, entity_type="invoice", entity_id=invoice.id,
            folio=invoice.folio, detail=detalle)
    flash(request, f"Factura {invoice.folio} guardada.", "ok")
    return RedirectResponse(
        f"/facturas/{invoice.id}/editar", status_code=status.HTTP_303_SEE_OTHER
    )


# --- duplicar ----------------------------------------------------------------


@app.get("/facturas/{invoice_id}/duplicar", response_class=HTMLResponse)
def invoice_duplicate_form(request: Request, invoice_id: int, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    source = db.get(Invoice, invoice_id)
    if source is None:
        flash(request, "Esa factura ya no existe.", "error")
        return RedirectResponse("/facturas", status_code=status.HTTP_303_SEE_OTHER)

    return render(
        request,
        "duplicate.html",
        db,
        active_view="invoices",
        page_title="Duplicar pre-factura",
        page_sub="Mismo vehículo · nuevo cliente · nuevo folio",
        source=source,
        # El folio que se propone es informativo: el definitivo se asigna al
        # confirmar, por si entretanto se creara otra factura.
        folio_previsto=_folio_preview(db),
        historial=inv_service.vin_history(db, source.vehicle_vin, source.id),
        comprometidas=inv_service.committed_siblings(db, source.vehicle_vin, source.id),
    )


def _folio_preview(db: Session) -> str:
    """Folio que tocaria, sin consumirlo. Solo para enseñarlo en pantalla."""
    folio = inv_service.next_folio(db)
    db.rollback()
    return folio


@app.post("/facturas/{invoice_id}/duplicar")
async def invoice_duplicate(request: Request, invoice_id: int, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    source = db.get(Invoice, invoice_id)
    if source is None:
        flash(request, "Esa factura ya no existe.", "error")
        return RedirectResponse("/facturas", status_code=status.HTTP_303_SEE_OTHER)

    form = await request.form()
    copia = inv_service.duplicate(db, source, form)
    db.commit()
    act.log(db, act.INVOICE_DUPLICATED, request=request, entity_type="invoice",
            entity_id=copia.id, folio=copia.folio, detail=f"copia de {source.folio}")
    flash(
        request,
        f"Creada la copia {copia.folio} a partir de {source.folio}. "
        "Nace como borrador: duplicar no confirma la reserva.",
        "ok",
    )
    return RedirectResponse(f"/facturas/{copia.id}/editar", status_code=status.HTTP_303_SEE_OTHER)


# --- historial por vehiculo --------------------------------------------------


@app.get("/vehiculos", response_class=HTMLResponse)
def vehicles(request: Request, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    return render(
        request,
        "vehicles.html",
        db,
        active_view="invoices",
        page_title="Vehículos",
        page_sub="Facturas agrupadas por VIN: un vehículo puede tener varios interesados.",
        grupos=inv_service.vin_groups(db),
    )


@app.get("/vehiculos/{vin}", response_class=HTMLResponse)
def vehicle_detail(request: Request, vin: str, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    historial = inv_service.vin_history(db, vin.upper())
    if not historial:
        flash(request, f"No hay facturas para el VIN {vin}.", "error")
        return RedirectResponse("/vehiculos", status_code=status.HTTP_303_SEE_OTHER)

    return render(
        request,
        "vehicle_detail.html",
        db,
        active_view="invoices",
        page_title="Historial del vehículo",
        page_sub=next((i.vehicle_title for i in historial if i.vehicle_title), vin.upper()),
        vin=vin.upper(),
        historial=historial,
        comprometidas=[i for i in historial if i.status in inv_service.COMMITTED_STATUSES],
    )


# --- plantillas --------------------------------------------------------------


@app.get("/plantillas", response_class=HTMLResponse)
def templates_view(request: Request, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user

    # Se comprueba de verdad que los tres archivos aprobados esten en su sitio.
    # Un panel que diga "Activa" sin haber mirado el disco no sirve de nada.
    plantillas = []
    for code, market in MARKETS.items():
        ruta = settings.data_dir.parent / "templates_html" / market.template
        existe = ruta.exists()
        plantillas.append(
            {
                "market": market,
                "path": f"/templates_html/{market.template}",
                "exists": existe,
                "size_kb": round(ruta.stat().st_size / 1024, 1) if existe else 0,
                "count": db.execute(
                    select(func.count(Invoice.id)).where(Invoice.locale == code)
                ).scalar_one(),
            }
        )

    return render(
        request,
        "templates.html",
        db,
        active_view="templates",
        page_title="Plantillas",
        page_sub="Tres HTML aprobados, un CSS compartido y reglas por mercado.",
        plantillas=plantillas,
    )


# --- actividad ---------------------------------------------------------------


@app.get("/actividad", response_class=HTMLResponse)
def activity_view(request: Request, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    entradas = db.execute(
        select(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(200)
    ).scalars().all()
    return render(
        request,
        "activity.html",
        db,
        active_view="activity",
        page_title="Actividad",
        page_sub="Registro básico: quién, qué y cuándo.",
        entries=entradas,
        action_labels=act.LABELS,
    )


# --- configuracion -----------------------------------------------------------


@app.get("/configuracion", response_class=HTMLResponse)
def settings_view(request: Request, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user

    if not master_unlocked(request):
        # Configuracion arranca bloqueada siempre, aunque la sesion del panel
        # este abierta. Es la segunda barrera que pidio el cliente.
        return render(
            request,
            "settings_locked.html",
            db,
            active_view="settings",
            page_title="Configuración",
            page_sub="Área protegida · Super-admin",
            error=request.session.pop("_master_error", None),
            minutes=settings.master_session_minutes,
        )

    touch_master(request)
    filas = db.execute(select(Setting).order_by(Setting.market, Setting.key)).scalars().all()
    globales = [s for s in filas if s.market is None]
    por_mercado: dict[str, list[Setting]] = {code: [] for code in MARKETS}
    for s in filas:
        if s.market in por_mercado:
            por_mercado[s.market].append(s)

    return render(
        request,
        "settings.html",
        db,
        active_view="settings",
        page_title="Configuración",
        page_sub="Segunda capa de seguridad para datos bancarios, marca, QR y plantillas.",
        globales=globales,
        por_mercado=por_mercado,
        minutes=settings.master_session_minutes,
    )


@app.post("/configuracion/desbloquear")
def settings_unlock(
    request: Request, master_password: str = Form(...), db: Session = Depends(get_db)
):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user

    if check_master(db, master_password):
        unlock_master(request)
        act.log(db, act.MASTER_UNLOCK, request=request)
    else:
        act.log(db, act.MASTER_FAILED, request=request)
        request.session["_master_error"] = "Master Password incorrecta."
    return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/configuracion/bloquear")
def settings_lock(request: Request, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    lock_master(request)
    act.log(db, act.MASTER_LOCK, request=request)
    flash(request, "Configuración bloqueada.", "ok")
    return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/configuracion/contrasenas")
def change_passwords(
    request: Request,
    which: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    username: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    if not master_unlocked(request):
        return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)

    if which not in (CRED_ADMIN, CRED_MASTER):
        flash(request, "Petición no válida.", "error")
    elif new_password != confirm_password:
        flash(request, "Las dos contraseñas no coinciden.", "error")
    elif len(new_password) < 8:
        flash(request, "La contraseña debe tener al menos 8 caracteres.", "error")
    else:
        set_password(
            db,
            which,
            new_password,
            username=(username.strip() or None) if which == CRED_ADMIN else None,
        )
        act.log(db, act.PASSWORD_CHANGED, request=request, detail=which)
        etiqueta = "de acceso al panel" if which == CRED_ADMIN else "Master Password"
        flash(request, f"Contraseña {etiqueta} actualizada.", "ok")

    return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)


# --- salud -------------------------------------------------------------------


@app.get("/salud", include_in_schema=False)
def health(db: Session = Depends(get_db)):
    """Sirve para que el servidor o Docker sepan si la aplicacion esta viva."""
    db.execute(select(func.count(Invoice.id))).scalar_one()
    return {"ok": True, "version": settings.app_version, "utc": utcnow().isoformat()}
