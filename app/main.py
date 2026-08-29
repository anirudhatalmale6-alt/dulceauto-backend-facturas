"""
Aplicacion FastAPI del backend de facturas.

Fase A: base del proyecto, acceso, Master Password, modelo de datos y el panel
completo con sus seis vistas y sus tres modos visuales.

Fase B: crear, editar, guardar borrador, buscar, duplicar y agrupacion por VIN.

La vista previa real y el PDF llegan en las fases C y D. Donde todavia no estan
cableadas, la pantalla lo dice con una etiqueta en lugar de ofrecer un boton que
no hace nada.
"""
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import activity as act
from . import callcenter as cc
from . import codes
from . import doctypes
from . import documents as doc_engine
from . import invoices as inv_service
from . import pdf as pdf_engine
from . import uploads
from .config import BASE_DIR, settings
from .db import Base, engine, get_db
from .fields import EDITABLE_FIELDS
from .locales import DELIVERY_MODES, MARKETS, delivery_label, format_amount, get_market
from .models import (
    CRED_ADMIN,
    CRED_MASTER,
    CRED_OPERATOR,
    NOTE_CUSTOMER,
    NOTE_FAQ,
    NOTE_TYPES,
    ROLE_ADMIN,
    ROLE_OPERATOR,
    STATUS_CANCELLED,
    STATUS_DELIVERED,
    STATUS_DRAFT,
    STATUS_PENDING,
    STATUS_SCHEDULED,
    STATUS_VALIDATED,
    ActivityLog,
    BrandProfile,
    Credential,
    FolioLedger,
    Invoice,
    InvoicePhoto,
    OperatorFaq,
    OperatorNote,
    Setting,
    utcnow,
)
from .security import (
    check_admin,
    check_master,
    check_operator,
    current_role,
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

# La cookie de sesion va firmada, y en produccion viaja solo por HTTPS. El
# valor viene del entorno (HTTPS_ONLY): en el VPS con certificado va a true y en
# local a false, porque sin certificado la sesion no llegaria a abrirse.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="da_session",
    same_site="lax",
    https_only=settings.https_only,
    max_age=settings.session_minutes * 60,
)

@app.middleware("http")
async def paginas_siempre_frescas(request: Request, call_next):
    """Las PAGINAS se revalidan siempre; los archivos con version, no.

    Poner la version detras del CSS no basta por si solo: si el navegador se
    queda tambien con la PAGINA de ayer, esa pagina sigue pidiendo la version de
    ayer y el arreglo no llega igualmente. Hay que cerrar los dos lados.

    Ninguna respuesta del panel lleva cabeceras de cache, y sin ellas el
    navegador aplica una caducidad que se inventa a partir de la antiguedad del
    archivo. Con `no-cache` se le obliga a preguntar antes de reutilizar nada.
    No es `no-store`: se puede seguir guardando, pero no servir sin confirmar.

    Para los estaticos es al reves: como la URL cambia sola cuando cambia el
    archivo (ver `estatico`), se pueden guardar mucho tiempo sin riesgo.

    Ademas ninguna pantalla del panel deberia quedarse en el disco de un equipo
    compartido: llevan datos de facturas y de clientes.
    """
    respuesta = await call_next(request)
    ruta = request.url.path
    # La lista de rutas cacheables es explicita, no un "lleva ?v= en la URL":
    # con esa regla, escribir a mano /operador?v=... convertiria una pantalla
    # con datos de un cliente en algo que el navegador guarda un ano.
    cacheable = ruta.startswith("/static/") or ruta == "/operador/logo.img"
    if cacheable and "v" in request.query_params:
        respuesta.headers.setdefault("Cache-Control", "public, max-age=31536000")
    elif "text/html" in respuesta.headers.get("content-type", ""):
        respuesta.headers["Cache-Control"] = "no-cache, must-revalidate"
    return respuesta


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
# Archivos de las plantillas aprobadas (CSS, tipografias e imagenes). Se montan
# aparte de los del panel: son de las facturas y no deben mezclarse con los del
# backend, porque la Fase D tiene que poder copiarlos tal cual dentro de cada
# PDF sin arrastrar nada del panel.
app.mount(
    "/plantillas/assets",
    StaticFiles(directory=doc_engine.TEMPLATES_DIR / "assets"),
    name="plantillas_assets",
)
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def estatico(ruta: str) -> str:
    """URL de un archivo estatico con su version detras: /static/x.css?v=...

    Sin esto, el navegador se queda con la hoja de estilos que descargo la
    primera vez. Es un problema real y no teorico: al desplegar el logotipo del
    Call Center, el HTML nuevo llego con la etiqueta <img> y el CSS que la
    encuadra seguia siendo el viejo en cache. Resultado: el logotipo pintado a
    tamano natural, ocupando la pantalla entera, con el servidor sirviendo el
    archivo correcto. Nada en el servidor lo delataba.

    La version es la fecha del propio archivo, asi que cambia sola en cuanto se
    toca y no hay que acordarse de subir ningun numero a mano. Si el archivo no
    esta, se devuelve la ruta pelada en vez de reventar: una version que falta
    no puede tumbar la pagina entera.
    """
    limpia = ruta.lstrip("/")
    try:
        marca = int((BASE_DIR / "static" / limpia).stat().st_mtime)
    except OSError:
        return f"/static/{limpia}"
    return f"/static/{limpia}?v={marca}"


# Disponible en todas las plantillas sin tener que pasarla en cada contexto.
templates.env.globals["estatico"] = estatico


def url_logo_callcenter(db: Session) -> str | None:
    """URL del logotipo del Call Center, con version, o None si no hay ninguno.

    La ruta es siempre la misma (/operador/logo.img), asi que sin version el
    navegador se queda con la imagen que descargo la primera vez: quien
    reemplace su logotipo seguiria viendo el anterior hasta vaciar la cache, y
    diria -con razon- que el boton de reemplazar no funciona.

    De version sirve el propio nombre del archivo guardado, que lo genera el
    servidor al azar y por tanto es distinto en cada subida. No hace falta
    inventar nada aparte ni tocar la base de datos.
    """
    ruta = cc.ajuste(db, cc.AJUSTE_LOGO)
    if not ruta:
        return None
    return f"/operador/logo.img?v={Path(ruta).stem}"

NAV_ITEMS = [
    {"key": "dashboard", "label": "Dashboard", "icon": "⌂", "url": "/"},
    {"key": "invoices", "label": "Facturas", "icon": "▤", "url": "/facturas"},
    {"key": "editor", "label": "Crear / Editar", "icon": "✎", "url": "/facturas/nueva"},
    {"key": "brands", "label": "Marcas", "icon": "◈", "url": "/marcas"},
    {"key": "templates", "label": "Plantillas", "icon": "◫", "url": "/plantillas"},
    {"key": "faqs", "label": "Guía Call Center", "icon": "◇", "url": "/guia"},
    {"key": "notes", "label": "Notas", "icon": "✐", "url": "/notas"},
    {"key": "activity", "label": "Actividad", "icon": "◷", "url": "/actividad"},
    {"key": "settings", "label": "Configuración", "icon": "⚙", "url": "/configuracion"},
]

# Nombre del estado en el panel. El del documento es otro y vive en locales.py:
# "delivered" aqui es "Entregada" y en la factura "Entrega completada".
STATUS_LABELS = {
    STATUS_DRAFT: ("Borrador", "muted"),
    STATUS_PENDING: ("Pago pendiente", "pending"),
    STATUS_VALIDATED: ("Pago validado", "generated"),
    STATUS_SCHEDULED: ("Entrega coordinada", "generated"),
    STATUS_DELIVERED: ("Entregada", "generated"),
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
        "delivery_modes": DELIVERY_MODES,
        "delivery_label": delivery_label,
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
    """Puerta unica de TODO el panel de administracion.

    Este es el punto que pidio el cliente: el Operador no queda fuera porque no
    se le pinte el boton, sino porque cualquier vista de Admin pasa por aqui.
    Escribir una direccion de Admin a mano con sesion de Operador acaba en esta
    misma comprobacion y no llega al codigo de la vista.

    Al Operador se le devuelve a su propio panel en vez de a /acceso: mandarlo
    al formulario de acceso teniendo sesion valida haria pensar que su sesion
    ha caducado, cuando lo que pasa es que esa pagina no es suya.
    """
    user = current_user(request)
    if not user:
        return RedirectResponse("/acceso", status_code=status.HTTP_303_SEE_OTHER)
    if current_role(request) != ROLE_ADMIN:
        _registrar_bloqueo(request, user)
        return RedirectResponse("/operador", status_code=status.HTTP_303_SEE_OTHER)
    return user


def _registrar_bloqueo(request: Request, user: str) -> None:
    """Deja constancia de que una sesion de Operador intento abrir Administracion.

    Abre su propia sesion de base de datos porque require_login se llama desde
    decenas de rutas y no recibe la del Depends. Si el registro fallara, el
    bloqueo tiene que seguir ocurriendo igual: por eso el except no re-lanza.
    Un fallo al anotar no puede convertirse en un fallo al proteger.
    """
    from .db import SessionLocal

    try:
        with SessionLocal() as db:
            act.log(
                db,
                act.OPERATOR_DENIED,
                actor=user,
                request=request,
                detail=request.url.path,
            )
    except Exception:  # noqa: BLE001
        pass


def require_operator(request: Request) -> str | RedirectResponse:
    """Puerta del modulo de Call Center.

    Admite tambien al Admin: el alcance prohibe que el Operador entre en
    Administracion, no al reves, y el propietario necesita poder abrir el
    modulo para revisarlo sin tener que salirse de su sesion.
    """
    user = current_user(request)
    if not user:
        return RedirectResponse("/operador/acceso", status_code=status.HTTP_303_SEE_OTHER)
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
        login_session(request, username, ROLE_ADMIN)
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
def invoices(
    request: Request, q: str = "", archivadas: int = 0, db: Session = Depends(get_db)
):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user

    stmt = select(Invoice).order_by(Invoice.updated_at.desc())
    # Las archivadas no salen en el listado normal. Siguen existiendo enteras y
    # se ven marcando la casilla; archivar no es borrar.
    ver_archivadas = bool(archivadas)
    if not ver_archivadas:
        stmt = stmt.where(Invoice.archived_at.is_(None))
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
        notas_por_factura=cc.contar_notas_por_factura(db),
        active_view="invoices",
        page_title="Facturas",
        page_sub="Consulta la factura enviada, edita o duplica una pre-factura.",
        invoices=filas,
        q=termino,
        interesados=interesados,
        ver_archivadas=ver_archivadas,
        n_archivadas=db.execute(
            select(func.count(Invoice.id)).where(Invoice.archived_at.is_not(None))
        ).scalar_one(),
        # Que se puede hacer con cada una: archivar siempre, eliminar solo si
        # esta cancelada y nunca llego a emitir documento.
        eliminables={
            f.id for f in filas if inv_service.motivo_para_no_eliminar(db, f) is None
        },
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
        fotos={f.position: f for f in invoice.photos} if invoice else {},
        comprometidas=[i for i in historial if i.status in inv_service.COMMITTED_STATUSES],
        marcas=inv_service.perfiles_activos(db),
        folio_ejemplo=inv_service.folio_previsto(db),
        # Las notas del Call Center de ESTA factura, para verlas sin salir de
        # aqui. Solo se leen: el Admin las revisa, no las reescribe.
        notas=cc.notas_de(db, invoice.id) if invoice else [],
        note_labels=cc.NOTE_LABELS,
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

    # Modo Manual: es una funcion administrativa, no algo del dia a dia, asi que
    # se exige la Master Password igual que en Configuracion. Sin ella se sigue
    # en Automatico, que es el modo normal.
    folio_manual = None
    if (form.get("folio_mode") or "auto") == "manual":
        if not master_unlocked(request):
            db.rollback()
            return editor_page(
                request,
                db,
                None,
                errors=[
                    "El folio manual es una función administrativa: desbloquea la "
                    "Configuración con la Master Password y vuelve a intentarlo."
                ],
                form=form,
            )
        touch_master(request)
        try:
            folio_manual = inv_service.normalizar_folio_manual(db, form.get("folio_manual"))
        except inv_service.FolioManualInvalido as exc:
            db.rollback()
            return editor_page(request, db, None, errors=[str(exc)], form=form)

    # Se valida antes de entrar al bucle de guardado: un error del operador no
    # tiene por que gastar folios ni reintentos.
    _, errores = inv_service.create(db, form, folio_manual)
    if errores:
        # Rollback para que el contador de folios no se gaste con un intento
        # fallido: si no, cada error dejaria un hueco en la numeracion.
        db.rollback()
        return editor_page(request, db, None, errors=errores, form=form)
    db.rollback()

    # Y se guarda reintentando: la cuenta Admin es compartida y dos operadores
    # pueden crear una factura a la vez. Si el folio se ocupa entretanto, se
    # coge el siguiente libre en lugar de enseñar un error de base de datos.
    #
    # Con folio manual NO se reintenta: coger otro numero en silencio significaria
    # que el Admin cree haber emitido uno y en la base hay otro distinto.
    try:
        invoice = inv_service.commit_creation(
            db,
            lambda s: inv_service.create(s, form, folio_manual)[0],
            reintentar=folio_manual is None,
        )
    except inv_service.FolioOcupado as exc:
        db.rollback()
        return editor_page(request, db, None, errors=[str(exc)], form=form)

    if folio_manual:
        # El contador se pone por delante del folio escrito a mano, para no
        # llegar mas adelante a un numero ya usado.
        if inv_service.avanzar_contador_tras_manual(db, invoice.folio):
            db.commit()
        act.log(
            db,
            act.SETTINGS_UPDATED,
            request=request,
            entity_type="invoice",
            entity_id=invoice.id,
            folio=invoice.folio,
            detail="folio asignado a mano (Master Password)",
        )
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
    # La marca se puede cambiar mientras la factura siga viva, y al cambiarla se
    # vuelven a congelar nombre y titulo. Lo que ya se emitio no se toca: cada
    # snapshot lleva su propia copia del logotipo y del icono.
    inv_service.cambiar_marca(db, invoice, form)

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
    folio_origen = source.folio
    # Igual que al crear: si otro operador se lleva el folio, se reintenta con
    # el siguiente libre. Tras un rollback la factura de origen queda caducada,
    # asi que se vuelve a leer dentro del propio intento.
    copia = inv_service.commit_creation(
        db, lambda s: inv_service.duplicate(s, s.get(Invoice, invoice_id), form)
    )
    act.log(db, act.INVOICE_DUPLICATED, request=request, entity_type="invoice",
            entity_id=copia.id, folio=copia.folio, detail=f"copia de {folio_origen}")
    flash(
        request,
        f"Creada la copia {copia.folio} a partir de {folio_origen}. "
        "Nace como borrador y con los datos bancarios de Configuración: "
        "duplicar no confirma la reserva.",
        "ok",
    )
    return RedirectResponse(f"/facturas/{copia.id}/editar", status_code=status.HTTP_303_SEE_OTHER)


# --- vista previa con la plantilla real --------------------------------------
#
# Son dos rutas y no una a proposito. /documento devuelve el documento tal cual,
# sin nada del panel alrededor: es lo que se ve, lo que se imprime y lo que la
# Fase D le va a dar a Chromium para hacer el PDF. Si la vista previa fuese un
# trozo de HTML incrustado dentro del panel, se estaria comprobando algo que no
# es lo que se entrega.

ZOOMS = (0.5, 0.75, 1.0)


# --- archivar y eliminar -----------------------------------------------------
#
# Regla acordada con el cliente: archivar es lo normal; eliminar de verdad solo
# se permite con una factura Cancelada que nunca llego a emitir documento, y
# pasando la Master Password. En los dos casos el folio queda reservado para
# siempre en el registro: eliminar no libera nunca un numero.


@app.post("/facturas/{invoice_id}/archivar")
def invoice_archive(request: Request, invoice_id: int, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        flash(request, "Esa factura ya no existe.", "error")
        return RedirectResponse("/facturas", status_code=status.HTTP_303_SEE_OTHER)

    archivada = invoice.archived_at is None
    if archivada:
        inv_service.archivar(db, invoice)
    else:
        inv_service.desarchivar(db, invoice)
    db.commit()
    act.log(
        db,
        act.INVOICE_UPDATED,
        request=request,
        entity_type="invoice",
        entity_id=invoice.id,
        folio=invoice.folio,
        detail="archivada" if archivada else "desarchivada",
    )
    flash(
        request,
        f"Factura {invoice.folio} {'archivada' if archivada else 'devuelta al listado'}. "
        "Conserva su folio, sus documentos y su historial.",
        "ok",
    )
    return RedirectResponse("/facturas", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/facturas/{invoice_id}/eliminar")
def invoice_delete(request: Request, invoice_id: int, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        flash(request, "Esa factura ya no existe.", "error")
        return RedirectResponse("/facturas", status_code=status.HTTP_303_SEE_OTHER)

    if not master_unlocked(request):
        flash(
            request,
            "Eliminar una factura es una función administrativa: desbloquea la "
            "Configuración con la Master Password y vuelve a intentarlo.",
            "error",
        )
        return RedirectResponse("/facturas", status_code=status.HTTP_303_SEE_OTHER)
    touch_master(request)

    motivo = inv_service.motivo_para_no_eliminar(db, invoice)
    if motivo:
        flash(request, motivo, "error")
        return RedirectResponse("/facturas", status_code=status.HTTP_303_SEE_OTHER)

    # El registro de la actividad se escribe ANTES de borrar: despues la factura
    # ya no tiene id que anotar. La fila del historial sobrevive igualmente,
    # porque su entity_id es un entero suelto y sin clave foranea.
    act.log(
        db,
        act.INVOICE_UPDATED,
        request=request,
        entity_type="invoice",
        entity_id=invoice.id,
        folio=invoice.folio,
        detail="eliminada (cancelada y sin documento emitido)",
    )
    folio = inv_service.eliminar(db, invoice)
    db.commit()
    flash(
        request,
        f"Factura {folio} eliminada. El folio queda reservado para siempre y no "
        "se volverá a emitir.",
        "ok",
    )
    return RedirectResponse("/facturas", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/facturas/{invoice_id}/documento", response_class=HTMLResponse)
def invoice_document(
    request: Request,
    invoice_id: int,
    doc: str = doctypes.FACTURA,
    db: Session = Depends(get_db),
):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        flash(request, "Esa factura ya no existe.", "error")
        return RedirectResponse("/facturas", status_code=status.HTTP_303_SEE_OTHER)
    tipo = doctypes.tipo(doc)
    if not doctypes.existe_para(tipo.clave, invoice.locale):
        tipo = doctypes.tipo(doctypes.FACTURA)
    return HTMLResponse(
        doc_engine.render(
            invoice, codigos="panel", doc=tipo.clave, **_marca_para_pantalla(db, invoice)
        ).html
    )


@app.post("/facturas/{invoice_id}/fotos")
async def invoice_photos(request: Request, invoice_id: int, db: Session = Depends(get_db)):
    """Sube las fotografias del vehiculo. Cuatro posiciones, como el diseno."""
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        flash(request, "Esa factura ya no existe.", "error")
        return RedirectResponse("/facturas", status_code=status.HTTP_303_SEE_OTHER)

    form = await request.form()
    puestas, errores = [], []
    for posicion in range(1, 5):
        archivo = form.get(f"foto_{posicion}")
        if archivo is None or not getattr(archivo, "filename", ""):
            continue
        try:
            guardado = uploads.guardar_imagen(
                await archivo.read(), archivo.filename, f"facturas/{invoice.id}"
            )
        except uploads.SubidaInvalida as exc:
            errores.append(f"Foto {posicion}: {exc}")
            continue

        anterior = next((f for f in invoice.photos if f.position == posicion), None)
        if anterior is not None:
            uploads.borrar(anterior.file_path)
            anterior.file_path = guardado.relativa
            anterior.original_name = archivo.filename[:255]
        else:
            db.add(
                InvoicePhoto(
                    invoice_id=invoice.id,
                    position=posicion,
                    file_path=guardado.relativa,
                    original_name=archivo.filename[:255],
                )
            )
        puestas.append(posicion)

    if puestas:
        db.commit()
        act.log(
            db,
            act.INVOICE_UPDATED,
            request=request,
            entity_type="invoice",
            entity_id=invoice.id,
            folio=invoice.folio,
            detail=f"fotografías {', '.join(str(p) for p in puestas)}",
        )
        flash(
            request,
            f"{len(puestas)} fotografía{'' if len(puestas) == 1 else 's'} actualizada"
            f"{'' if len(puestas) == 1 else 's'}. Se usarán en el próximo PDF que genere.",
            "ok",
        )
    for e in errores:
        flash(request, e, "error")
    if not puestas and not errores:
        flash(request, "No se ha elegido ninguna fotografía.", "error")
    return RedirectResponse(
        f"/facturas/{invoice_id}/editar", status_code=status.HTTP_303_SEE_OTHER
    )


@app.post("/facturas/{invoice_id}/fotos/{posicion}/quitar")
def invoice_photo_remove(
    request: Request, invoice_id: int, posicion: int, db: Session = Depends(get_db)
):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    invoice = db.get(Invoice, invoice_id)
    if invoice is not None:
        foto = next((f for f in invoice.photos if f.position == posicion), None)
        if foto is not None:
            uploads.borrar(foto.file_path)
            db.delete(foto)
            db.commit()
            flash(
                request,
                f"Fotografía {posicion} retirada. Vuelve a usarse la del diseño aprobado.",
                "ok",
            )
    return RedirectResponse(
        f"/facturas/{invoice_id}/editar", status_code=status.HTTP_303_SEE_OTHER
    )


@app.get("/facturas/{invoice_id}/foto/{posicion}")
def invoice_photo_file(
    request: Request, invoice_id: int, posicion: int, db: Session = Depends(get_db)
):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        return Response(status_code=404)
    foto = next((f for f in invoice.photos if f.position == posicion), None)
    ruta = uploads.ruta_absoluta(foto.file_path if foto else None)
    if ruta is None:
        return Response(status_code=404)
    return FileResponse(ruta)


@app.get("/facturas/{invoice_id}/codigo-qr")
@app.get("/facturas/{invoice_id}/codigo-qr.svg")   # nombre anterior, se conserva
def invoice_qr(request: Request, invoice_id: int, db: Session = Depends(get_db)):
    """QR de verificacion de esa factura.

    En el modo normal se dibuja al vuelo y se sirve como SVG, no como imagen:
    en el PDF se imprime nitido a cualquier tamano y un lector de codigos no se
    atraganta con los bordes. Si en Configuracion hay un QR subido a mano, se
    sirve ese archivo tal cual, con su propio tipo.
    """
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        return Response(status_code=404)
    # Si en Configuracion hay puesto un QR a mano, es ese el que se sirve, para
    # que el documento y la vista previa ensenen exactamente lo que se va a
    # imprimir. Sin el, se dibuja el de siempre a partir del folio.
    manual = codes.qr_fijo(db)
    if manual is not None:
        return FileResponse(manual)
    base = (invoice.verify_url_base or "").strip()
    url = base.rstrip("/") + "/" + (invoice.folio or "") if base else ""
    return Response(codes.qr_svg(url), media_type="image/svg+xml")


@app.get("/facturas/{invoice_id}/codigo-barras.svg")
def invoice_barcode(request: Request, invoice_id: int, db: Session = Depends(get_db)):
    """Code 128-B del folio."""
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        return Response(status_code=404)
    return Response(codes.barcode_svg(invoice.folio or ""), media_type="image/svg+xml")


@app.get("/facturas/{invoice_id}/vista-previa", response_class=HTMLResponse)
def invoice_preview(
    request: Request,
    invoice_id: int,
    zoom: float = 0.75,
    doc: str = doctypes.FACTURA,
    db: Session = Depends(get_db),
):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        flash(request, "Esa factura ya no existe.", "error")
        return RedirectResponse("/facturas", status_code=status.HTTP_303_SEE_OTHER)

    tipo = doctypes.tipo(doc)
    if not doctypes.existe_para(tipo.clave, invoice.locale):
        # Un documento que no existe para ese mercado no da error: se ensena la
        # pre-factura, que existe siempre, y se dice por que.
        flash(
            request,
            f"«{tipo.nombre}» todavía no está preparado para {get_market(invoice.locale).label}. "
            "Se muestra la pre-factura.",
            "error",
        )
        tipo = doctypes.tipo(doctypes.FACTURA)

    documento = doc_engine.render(invoice, doc=tipo.clave, **_marca_para_pantalla(db, invoice))
    market = get_market(invoice.locale)
    return render(
        request,
        "preview.html",
        db,
        active_view="invoices",
        page_title="Vista previa",
        page_sub=f"{invoice.folio} · {tipo.nombre} · plantilla {market.label}",
        invoice=invoice,
        market=market,
        documento=documento,
        snapshots=pdf_engine.snapshots_de(db, invoice.id, tipo.clave),
        zoom=zoom if zoom in ZOOMS else 0.75,
        zooms=ZOOMS,
        etiquetas=doc_engine.ETIQUETAS_HUECO,
        tipo_doc=tipo,
        tipos_doc=_tipos_disponibles(invoice),
        doc_sugerido=_documento_del_estado(db, invoice.status),
        # La MISMA funcion que usa el motor para decidir si deja generar, para
        # que el boton no ofrezca algo que el servidor va a rechazar.
        puede_generar=doctypes.puede_generarse(db, tipo.clave, invoice.status),
        estado_legible=STATUS_LABELS.get(invoice.status, (invoice.status,))[0],
    )


# --- PDF y snapshots ---------------------------------------------------------


@app.post("/facturas/{invoice_id}/pdf")
def invoice_pdf_create(
    request: Request,
    invoice_id: int,
    doc: str = doctypes.FACTURA,
    db: Session = Depends(get_db),
):
    """Genera el PDF y deja congelada la factura en una carpeta propia.

    La ruta es sincrona a proposito: FastAPI la ejecuta en un hilo aparte, que
    es donde Chromium puede trabajar. En una ruta async bloquearia el bucle de
    eventos y el panel se quedaria parado para todos mientras imprime.
    """
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        flash(request, "Esa factura ya no existe.", "error")
        return RedirectResponse("/facturas", status_code=status.HTTP_303_SEE_OTHER)

    tipo = doctypes.tipo(doc)
    vuelta = f"/facturas/{invoice_id}/vista-previa?doc={tipo.clave}"

    if invoice.status == STATUS_DRAFT:
        flash(
            request,
            "Un borrador no se imprime. Pásela a «Pago pendiente» cuando esté completa.",
            "error",
        )
        return RedirectResponse(vuelta, status_code=status.HTTP_303_SEE_OTHER)

    try:
        resultado = pdf_engine.generar(db, invoice, tipo.clave)
    except pdf_engine.PdfEstadoNoCorresponde as exc:
        # No se ha creado nada: la comprobacion va antes de repartir version y
        # de crear la carpeta. No hay nada que deshacer.
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(vuelta, status_code=status.HTTP_303_SEE_OTHER)
    except pdf_engine.PdfError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse(vuelta, status_code=status.HTTP_303_SEE_OTHER)

    db.commit()
    act.log(
        db,
        act.PDF_GENERATED,
        request=request,
        entity_type="invoice",
        entity_id=invoice.id,
        folio=invoice.folio,
        detail=f"{tipo.nombre}, versión {resultado.snapshot.version}",
    )
    flash(
        request,
        f"{tipo.nombre}: PDF generado (versión {resultado.snapshot.version}). "
        "Queda guardada una copia congelada con sus imágenes: no cambiará aunque "
        "mañana se cambie el logotipo o la cuenta bancaria. "
        "Los demás documentos de esta reserva no se han tocado.",
        "ok",
    )
    return RedirectResponse(vuelta, status_code=status.HTTP_303_SEE_OTHER)


@app.get("/facturas/{invoice_id}/pdf")
def invoice_pdf_download(
    request: Request,
    invoice_id: int,
    version: int = 0,
    doc: str = doctypes.FACTURA,
    db: Session = Depends(get_db),
):
    """Descarga un PDF ya generado. Sin version, el ultimo DE ESE documento."""
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    tipo = doctypes.tipo(doc)
    snapshots = pdf_engine.snapshots_de(db, invoice_id, tipo.clave)
    if version:
        snapshots = [s for s in snapshots if s.version == version]
    if not snapshots:
        flash(request, f"«{tipo.nombre}» todavía no tiene ningún PDF generado.", "error")
        return RedirectResponse(
            f"/facturas/{invoice_id}/vista-previa?doc={tipo.clave}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    snapshot = snapshots[0]
    ruta = pdf_engine.ruta_absoluta(snapshot.pdf_path)
    if ruta is None:
        # El registro existe pero el archivo no. Se dice, en lugar de devolver
        # un 500 que no explica nada.
        flash(
            request,
            f"El archivo del PDF versión {snapshot.version} no está en el disco. "
            "Vuelva a generarlo.",
            "error",
        )
        return RedirectResponse(
            f"/facturas/{invoice_id}/vista-previa?doc={tipo.clave}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return FileResponse(
        ruta,
        media_type="application/pdf",
        filename=f"{snapshot.folio}{tipo.sufijo_pdf}-v{snapshot.version}.pdf",
    )


@app.get("/plantillas/{locale}/documento", response_class=HTMLResponse)
def template_document(request: Request, locale: str, db: Session = Depends(get_db)):
    """Plantilla de un mercado con la ultima factura real de ese mercado. Sirve
    para mirar una plantilla sin tener que buscar antes una factura suya."""
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    if locale not in MARKETS:
        flash(request, "Ese mercado no existe.", "error")
        return RedirectResponse("/plantillas", status_code=status.HTTP_303_SEE_OTHER)
    invoice = db.execute(
        select(Invoice).where(Invoice.locale == locale).order_by(Invoice.id.desc()).limit(1)
    ).scalar_one_or_none()
    if invoice is None:
        flash(request, f"Todavía no hay ninguna factura de {MARKETS[locale].label}.", "error")
        return RedirectResponse("/plantillas", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(
        f"/facturas/{invoice.id}/vista-previa", status_code=status.HTTP_303_SEE_OTHER
    )


# --- historial por vehiculo --------------------------------------------------


# --- perfiles de marca -------------------------------------------------------
#
# Un perfil es una ficha de marca: nombre, logotipo, icono de "Compra segura" y
# titulo del documento. Nada mas. Cuentas bancarias, usuarios o datos fiscales
# por empresa serian un sistema multiempresa, que el cliente descarto.
#
# Los perfiles no se borran, se desactivan. Borrar uno dejaria sin logotipo a
# una factura todavia no emitida que lo estuviera usando.


def _tipos_disponibles(invoice: Invoice) -> list:
    """Documentos que se pueden ver y generar para esta factura.

    La pre-factura esta siempre. Los complementarios, solo en los mercados en
    los que existen: en esta fase, es-MX.
    """
    return [
        doctypes.TIPOS[c]
        for c in (doctypes.FACTURA, *doctypes.COMPLEMENTARIOS)
        if doctypes.existe_para(c, invoice.locale)
    ]


def _documento_del_estado(db: Session, estado: str) -> str | None:
    """Documento complementario que corresponde a ese estado.

    Delega en doctypes, que es donde vive la regla. El panel y el motor tienen
    que decir lo mismo: si el boton dijera una cosa y el servidor hiciera otra,
    el operador veria un boton que al pulsarlo le da un error.
    """
    return doctypes.documento_de_estado(db, estado)


def _marca_para_pantalla(db: Session, invoice: Invoice) -> dict:
    """Argumentos de marca para dibujar el documento en el panel.

    En el panel las imagenes se sirven por URL; en el snapshot son archivos
    copiados. De ahi que esto viva aqui y no en pdf.py.
    """
    perfil = db.get(BrandProfile, invoice.brand_profile_id) if invoice.brand_profile_id else None
    if perfil is None:
        # Factura anterior a los perfiles: se sigue viendo con el logotipo
        # global de Configuracion, que es lo que llevaba.
        return {
            "logo": "/configuracion/logo.img" if _logo_actual(db) else None,
            "marca": invoice.brand_name or "DulceAuto",
        }
    return {
        "logo": f"/marcas/{perfil.id}/logo.img" if perfil.logo_path else None,
        "safe_icon": f"/marcas/{perfil.id}/icono.img" if perfil.safe_icon_path else None,
        # El nombre y el titulo salen de la factura, donde estan congelados.
        "marca": invoice.brand_name or perfil.name,
        "doc_title": invoice.brand_doc_title or perfil.doc_title,
    }


@app.get("/marcas", response_class=HTMLResponse)
def brands_view(request: Request, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    perfiles = list(db.execute(select(BrandProfile).order_by(BrandProfile.name)).scalars())
    en_uso = {
        pid: n
        for pid, n in db.execute(
            select(Invoice.brand_profile_id, func.count(Invoice.id))
            .where(Invoice.brand_profile_id.is_not(None))
            .group_by(Invoice.brand_profile_id)
        )
    }
    return render(
        request,
        "brands.html",
        db,
        active_view="brands",
        page_title="Marcas",
        page_sub="Nombre, logotipo, icono de Compra segura y título del documento",
        perfiles=perfiles,
        en_uso=en_uso,
    )


async def _guardar_imagen_de_marca(form, campo: str, sub: str) -> tuple[str | None, str | None]:
    """Lee un archivo del formulario y lo guarda. (ruta, error)."""
    archivo = form.get(campo)
    if archivo is None or not getattr(archivo, "filename", ""):
        return None, None
    try:
        guardado = uploads.guardar_imagen(await archivo.read(), archivo.filename, sub)
    except uploads.SubidaInvalida as exc:
        return None, str(exc)
    return guardado.relativa, None


@app.post("/marcas/guardar")
async def brand_save(request: Request, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user

    form = await request.form()
    crudo = (form.get("id") or "").strip()
    perfil = db.get(BrandProfile, int(crudo)) if crudo.isdigit() else None

    nombre = (form.get("name") or "").strip()
    if not nombre:
        flash(request, "La marca necesita un nombre.", "error")
        return RedirectResponse("/marcas", status_code=status.HTTP_303_SEE_OTHER)

    logo_rel, error = await _guardar_imagen_de_marca(form, "logo", "marcas")
    if error:
        flash(request, f"Logotipo: {error}", "error")
        return RedirectResponse("/marcas", status_code=status.HTTP_303_SEE_OTHER)
    icono_rel, error = await _guardar_imagen_de_marca(form, "safe_icon", "marcas")
    if error:
        flash(request, f"Icono de Compra segura: {error}", "error")
        return RedirectResponse("/marcas", status_code=status.HTTP_303_SEE_OTHER)

    nuevo = perfil is None
    if perfil is None:
        perfil = BrandProfile(name=nombre)
        db.add(perfil)

    anterior_logo = perfil.logo_path
    anterior_icono = perfil.safe_icon_path
    perfil.name = nombre
    perfil.doc_title = (form.get("doc_title") or "").strip() or None
    if logo_rel:
        perfil.logo_path = logo_rel
    if icono_rel:
        perfil.safe_icon_path = icono_rel
    db.commit()

    # Los archivos viejos se borran despues de guardar, no antes: si el guardado
    # fallara se quedarian sin ninguno de los dos.
    if logo_rel and anterior_logo and anterior_logo != logo_rel:
        uploads.borrar(anterior_logo)
    if icono_rel and anterior_icono and anterior_icono != icono_rel:
        uploads.borrar(anterior_icono)

    act.log(
        db,
        act.SETTINGS_UPDATED,
        request=request,
        entity_type="brand_profile",
        entity_id=perfil.id,
        detail=f"marca {'creada' if nuevo else 'actualizada'}: {perfil.name}",
    )
    flash(
        request,
        f"Marca «{perfil.name}» {'creada' if nuevo else 'actualizada'}. "
        "Las facturas ya emitidas no cambian: cada PDF lleva dentro su propia copia.",
        "ok",
    )
    return RedirectResponse("/marcas", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/marcas/{brand_id}/activar")
def brand_toggle(request: Request, brand_id: int, db: Session = Depends(get_db)):
    """Activa o desactiva un perfil. No existe borrado, y es a proposito.

    Un perfil desactivado deja de ofrecerse al crear facturas nuevas, pero las
    que ya lo tienen conservan su marca y su logotipo.
    """
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    perfil = db.get(BrandProfile, brand_id)
    if perfil is None:
        flash(request, "Esa marca ya no existe.", "error")
        return RedirectResponse("/marcas", status_code=status.HTTP_303_SEE_OTHER)

    perfil.is_active = not perfil.is_active
    db.commit()
    act.log(
        db,
        act.SETTINGS_UPDATED,
        request=request,
        entity_type="brand_profile",
        entity_id=perfil.id,
        detail=f"marca {'activada' if perfil.is_active else 'desactivada'}: {perfil.name}",
    )
    flash(
        request,
        f"Marca «{perfil.name}» {'activada' if perfil.is_active else 'desactivada'}."
        + ("" if perfil.is_active else " Las facturas que ya la usan no cambian."),
        "ok",
    )
    return RedirectResponse("/marcas", status_code=status.HTTP_303_SEE_OTHER)


def _archivo_de_marca(db: Session, brand_id: int, campo: str):
    perfil = db.get(BrandProfile, brand_id)
    ruta = uploads.ruta_absoluta(getattr(perfil, campo, None)) if perfil else None
    if ruta is None:
        return Response(status_code=404)
    return FileResponse(ruta)


@app.get("/marcas/{brand_id}/logo.img")
def brand_logo_file(request: Request, brand_id: int, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    return _archivo_de_marca(db, brand_id, "logo_path")


@app.get("/marcas/{brand_id}/icono.img")
def brand_icon_file(request: Request, brand_id: int, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    return _archivo_de_marca(db, brand_id, "safe_icon_path")


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
                # Huecos que tiene de verdad el archivo, contados leyendolo.
                "huecos": len(doc_engine.huecos_de(code)) if existe else 0,
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
        huecos=doc_engine.huecos_de("es-MX"),
        etiquetas=doc_engine.ETIQUETAS_HUECO,
        sin_hueco=doc_engine.campos_sin_hueco("es-MX"),
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


# Nombre legible de cada ajuste. La clave tecnica se sigue viendo en el codigo,
# pero el operador no tiene por que saber que es "banking.beneficiary".
ETIQUETAS_AJUSTE = {
    "banking.bank": "Banco receptor",
    "banking.beneficiary": "Beneficiario",
    "banking.account_label": "Etiqueta de la cuenta",
    "banking.account_number": "Número de cuenta (CLABE / CBU)",
    "banking.bank_account": "Cuenta bancaria",
    "representative.name": "Representante",
    "representative.role": "Cargo",
    "representative.phone": "Teléfono",
    "representative.email": "Email",
    "representative.hours": "Horario de atención",
    "qr.base_url": "URL base del QR de verificación",
    "qr.mode": "Modo del QR",
    "qr.image_path": "Imagen de QR fija (si no se genera)",
    "brand.logo_path": "Logotipo",
    "pdf.engine": "Motor de PDF",
    "pdf.page_size": "Tamaño de página",
    "pdf.single_page": "Forzar una sola página",
    "folio.prefix": "Prefijo del folio",
    "folio.next": "Siguiente folio",
    "callcenter.operator_name": "Nombre visible del Operador",
    "callcenter.logo_path": "Logotipo del Call Center",
    "docs.por_estado.payment_validated": "Documento para «Pago validado»",
    "docs.por_estado.delivery_scheduled": "Documento para «Entrega coordinada»",
}

# Ajustes que NO salen en la rejilla generica de Configuracion porque tienen su
# propia tarjeta: los que se editan con un archivo y los del Call Center.
AJUSTES_CON_TARJETA_PROPIA = (
    "brand.logo_path",
    "qr.mode",
    "qr.image_path",
    cc.AJUSTE_NOMBRE,
    cc.AJUSTE_LOGO,
    *(doctypes.clave_ajuste(e) for e in doctypes.PAREJA_POR_DEFECTO),
)

# Estados que pueden llevar un documento complementario asociado, en el orden
# del guion de la operacion. Es la pareja que el cliente cerro por escrito; la
# tarjeta de Configuracion deja cambiarla sin tocar codigo.
ESTADOS_CON_DOCUMENTO = (STATUS_VALIDATED, STATUS_SCHEDULED)


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
        etiquetas=ETIQUETAS_AJUSTE,
        # Pareja estado -> documento, para su tarjeta propia.
        docs_por_estado=[
            {
                "estado": e,
                "nombre": STATUS_LABELS.get(e, (e,))[0],
                "clave": doctypes.clave_ajuste(e),
                "elegido": _documento_del_estado(db, e) or "",
            }
            for e in ESTADOS_CON_DOCUMENTO
        ],
        docs_complementarios=[doctypes.TIPOS[c] for c in doctypes.COMPLEMENTARIOS],
        operador_usuario=(
            (db.get(Credential, CRED_OPERATOR).username or "operador")
            if db.get(Credential, CRED_OPERATOR)
            else "operador"
        ),
        logo_url="/configuracion/logo.img" if _logo_actual(db) else None,
        ajustes_con_tarjeta=AJUSTES_CON_TARJETA_PROPIA,
        # Call Center. El nombre visible se pinta vacio si no se ha puesto
        # ninguno: la casilla en blanco es justo lo que significa "usa el
        # nombre de la cuenta", y rellenarla sola con "operador" haria creer
        # que hay un nombre puesto cuando no lo hay.
        callcenter_nombre=cc.ajuste(db, cc.AJUSTE_NOMBRE),
        callcenter_logo=url_logo_callcenter(db),
        qr_modo=codes.ajuste(db, "qr.mode") or codes.MODO_DINAMICO,
        qr_url="/configuracion/qr.img" if codes.ajuste(db, "qr.image_path") else None,
        # Modo manual con el archivo desaparecido: el sistema vuelve solo al QR
        # automatico, pero hay que decirlo o parecera que el ajuste no funciona.
        qr_perdido=(
            codes.ajuste(db, "qr.mode") == codes.MODO_FIJO and codes.qr_fijo(db) is None
        ),
        minutes=settings.master_session_minutes,
    )


def _logo_actual(db: Session) -> str | None:
    fila = db.execute(
        select(Setting).where(Setting.key == "brand.logo_path", Setting.market.is_(None))
    ).scalar_one_or_none()
    return fila.value if fila and fila.value else None


@app.post("/configuracion/guardar")
async def settings_save(request: Request, db: Session = Depends(get_db)):
    """Guarda los ajustes de un bloque de Configuracion.

    Nunca se crean claves nuevas desde el formulario: solo se actualizan las que
    ya existen. Un campo con un nombre inventado se ignora en lugar de sembrar
    la tabla de ajustes fantasma que nadie lee despues.
    """
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    if not master_unlocked(request):
        flash(request, "La Configuración está bloqueada.", "error")
        return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)

    touch_master(request)
    form = await request.form()
    market = form.get("market") or None

    cambios, errores = [], []
    for campo, valor in form.multi_items():
        if not campo.startswith("ajuste:"):
            continue
        clave = campo.split(":", 1)[1]
        fila = db.execute(
            select(Setting).where(Setting.key == clave, Setting.market == market)
        ).scalar_one_or_none()
        if fila is None:
            continue
        nuevo = (valor or "").strip()
        if nuevo == (fila.value or ""):
            continue

        problema = inv_service.validar_ajuste(clave, nuevo, market)
        if problema:
            errores.append(problema)
            continue
        cambios.append((clave, fila.value, nuevo))
        fila.value = nuevo

    if errores:
        db.rollback()
        for e in errores:
            flash(request, e, "error")
        return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)

    if not cambios:
        flash(request, "No había nada que cambiar.", "ok")
        return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)

    db.commit()
    act.log(
        db,
        act.SETTINGS_UPDATED,
        request=request,
        detail=f"{market or 'global'}: " + ", ".join(c[0] for c in cambios),
    )
    flash(
        request,
        f"Guardado. {len(cambios)} ajuste{'' if len(cambios) == 1 else 's'} actualizado"
        f"{'' if len(cambios) == 1 else 's'}. "
        "Las facturas ya emitidas no cambian: conservan los datos con los que se crearon.",
        "ok",
    )
    return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/configuracion/logo")
async def settings_logo(request: Request, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    if not master_unlocked(request):
        flash(request, "La Configuración está bloqueada.", "error")
        return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)

    touch_master(request)
    form = await request.form()
    archivo = form.get("logo")
    if archivo is None or not getattr(archivo, "filename", ""):
        flash(request, "No se ha elegido ningún archivo.", "error")
        return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)

    try:
        guardado = uploads.guardar_imagen(await archivo.read(), archivo.filename, "logo")
    except uploads.SubidaInvalida as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)

    fila = db.execute(
        select(Setting).where(Setting.key == "brand.logo_path", Setting.market.is_(None))
    ).scalar_one_or_none()
    anterior = fila.value if fila else None
    if fila is None:
        fila = Setting(key="brand.logo_path", market=None, value="", is_sensitive=True)
        db.add(fila)
    fila.value = guardado.relativa
    db.commit()

    # El anterior se borra despues de guardar el nuevo: si se borrara antes y
    # algo fallara al guardar, se quedarian sin ninguno.
    if anterior and anterior != guardado.relativa:
        uploads.borrar(anterior)

    act.log(request=request, db=db, action=act.SETTINGS_UPDATED, detail="logotipo actualizado")
    medida = f"{guardado.ancho}×{guardado.alto} px" if guardado.ancho else guardado.formato
    flash(
        request,
        f"Logotipo actualizado ({guardado.formato}, {medida}). "
        "Las facturas ya emitidas no cambian: cada PDF lleva dentro su propia copia.",
        "ok",
    )
    return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/configuracion/logo/quitar")
def settings_logo_remove(request: Request, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    if not master_unlocked(request):
        flash(request, "La Configuración está bloqueada.", "error")
        return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)

    touch_master(request)
    fila = db.execute(
        select(Setting).where(Setting.key == "brand.logo_path", Setting.market.is_(None))
    ).scalar_one_or_none()
    if fila and fila.value:
        uploads.borrar(fila.value)
        fila.value = ""
        db.commit()
        act.log(request=request, db=db, action=act.SETTINGS_UPDATED, detail="logotipo retirado")
        flash(request, "Logotipo retirado. La factura vuelve a la marca del diseño aprobado.", "ok")
    return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/configuracion/logo.img")
def settings_logo_file(request: Request, db: Session = Depends(get_db)):
    """Sirve el logotipo guardado. No se expone la carpeta de subidas entera."""
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    fila = db.execute(
        select(Setting).where(Setting.key == "brand.logo_path", Setting.market.is_(None))
    ).scalar_one_or_none()
    ruta = uploads.ruta_absoluta(fila.value if fila else None)
    if ruta is None:
        return Response(status_code=404)
    return FileResponse(ruta)


# --- logotipo del Call Center -------------------------------------------------
#
# Es un archivo distinto del de la factura, guardado en su propia carpeta y en
# su propio ajuste. Que sean dos y no uno es justo lo que pidio el cliente:
# cambiar la cabecera del panel de atencion no puede tocar el logotipo de las
# facturas ni el de ninguna marca.
#
# Quitarlo no deja el modulo sin nada: vuelve a la marca DulceAuto del diseno
# aprobado, que es la que se ve hoy.


@app.post("/configuracion/callcenter/logo")
async def settings_callcenter_logo(request: Request, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    if not master_unlocked(request):
        flash(request, "La Configuración está bloqueada.", "error")
        return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)

    touch_master(request)
    form = await request.form()
    archivo = form.get("logo")
    if archivo is None or not getattr(archivo, "filename", ""):
        flash(request, "No se ha elegido ningún archivo.", "error")
        return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)

    try:
        guardado = uploads.guardar_imagen(
            await archivo.read(), archivo.filename, "callcenter"
        )
    except uploads.SubidaInvalida as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)

    fila = _fila_ajuste(db, cc.AJUSTE_LOGO)
    anterior = fila.value
    fila.value = guardado.relativa
    db.commit()

    # Igual que con el logotipo de la factura: el anterior se borra despues de
    # haber guardado el nuevo, nunca antes.
    if anterior and anterior != guardado.relativa:
        uploads.borrar(anterior)

    act.log(
        request=request,
        db=db,
        action=act.SETTINGS_UPDATED,
        detail="logotipo del Call Center actualizado",
    )
    medida = f"{guardado.ancho}×{guardado.alto} px" if guardado.ancho else guardado.formato
    flash(
        request,
        f"Logotipo del Call Center actualizado ({guardado.formato}, {medida}). "
        "Solo cambia la cabecera del panel de atención: las facturas, sus PDF y "
        "los logotipos de las marcas no se tocan.",
        "ok",
    )
    return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/configuracion/callcenter/logo/quitar")
def settings_callcenter_logo_remove(request: Request, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    if not master_unlocked(request):
        flash(request, "La Configuración está bloqueada.", "error")
        return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)

    touch_master(request)
    fila = _fila_ajuste(db, cc.AJUSTE_LOGO)
    if fila.value:
        uploads.borrar(fila.value)
        fila.value = ""
        db.commit()
        act.log(
            request=request,
            db=db,
            action=act.SETTINGS_UPDATED,
            detail="logotipo del Call Center retirado",
        )
        flash(
            request,
            "Logotipo del Call Center retirado. La cabecera vuelve a la marca "
            "DulceAuto predeterminada.",
            "ok",
        )
    else:
        flash(request, "El Call Center ya estaba con la marca predeterminada.", "ok")
    return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/operador/logo.img")
def callcenter_logo_file(request: Request, db: Session = Depends(get_db)):
    """Sirve el logotipo del Call Center.

    Cuelga de /operador y pasa por require_operator, no por require_login: lo
    tiene que ver el Operador en su cabecera, y con require_login su sesion
    acabaria redirigida y la imagen no cargaria nunca. Es la misma puerta que
    ya protege el resto del modulo, no una excepcion nueva.

    Vale tambien para la vista previa de Configuracion, porque require_operator
    admite al Admin: un solo sitio lee el archivo, no dos.
    """
    user = require_operator(request)
    if isinstance(user, RedirectResponse):
        return user
    ruta = uploads.ruta_absoluta(cc.ajuste(db, cc.AJUSTE_LOGO))
    if ruta is None:
        return Response(status_code=404)
    return FileResponse(ruta)


# --- QR: automatico o subido a mano -------------------------------------------
#
# El modo normal es el automatico: el servidor dibuja el QR de cada factura con
# su enlace de verificacion, y no hay nada que mantener. El manual esta para
# cuando el QR viene de otro sistema y tiene que salir tal cual.
#
# Subir una imagen cambia el modo sola: nadie sube un QR para dejarlo apagado.
# Volver al automatico se hace con su propio boton y conserva el archivo, por si
# se quiere recuperar.


def _fila_ajuste(db: Session, clave: str) -> Setting:
    fila = db.execute(
        select(Setting).where(Setting.key == clave, Setting.market.is_(None))
    ).scalar_one_or_none()
    if fila is None:
        fila = Setting(key=clave, market=None, value="", is_sensitive=True)
        db.add(fila)
    return fila


@app.post("/configuracion/qr")
async def settings_qr_upload(request: Request, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    if not master_unlocked(request):
        flash(request, "La Configuración está bloqueada.", "error")
        return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)

    touch_master(request)
    form = await request.form()
    archivo = form.get("qr")
    if archivo is None or not getattr(archivo, "filename", ""):
        flash(request, "No se ha elegido ningún archivo.", "error")
        return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)

    try:
        guardado = uploads.guardar_imagen(await archivo.read(), archivo.filename, "qr")
    except uploads.SubidaInvalida as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)

    ruta = _fila_ajuste(db, "qr.image_path")
    modo = _fila_ajuste(db, "qr.mode")
    anterior = ruta.value
    ruta.value = guardado.relativa
    modo.value = codes.MODO_FIJO
    db.commit()
    if anterior and anterior != guardado.relativa:
        uploads.borrar(anterior)

    act.log(request=request, db=db, action=act.SETTINGS_UPDATED, detail="QR personalizado")
    medida = f"{guardado.ancho}×{guardado.alto} px" if guardado.ancho else guardado.formato
    aviso = ""
    if guardado.ancho and abs(guardado.ancho - guardado.alto) > max(guardado.ancho, guardado.alto) * 0.1:
        # Un QR es cuadrado. Si no lo es, o sobra fondo o la imagen no es un QR,
        # y en el hueco del diseno saldra deformada.
        aviso = " Atención: la imagen no es cuadrada y el hueco del QR sí lo es."
    flash(
        request,
        f"QR personalizado en uso ({guardado.formato}, {medida}). "
        "Compruebe con el móvil que se lee antes de emitir facturas. "
        "Las ya emitidas no cambian: cada PDF lleva dentro su propia copia." + aviso,
        "ok" if not aviso else "error",
    )
    return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/configuracion/qr/automatico")
def settings_qr_dynamic(request: Request, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    if not master_unlocked(request):
        flash(request, "La Configuración está bloqueada.", "error")
        return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)

    touch_master(request)
    _fila_ajuste(db, "qr.mode").value = codes.MODO_DINAMICO
    db.commit()
    act.log(request=request, db=db, action=act.SETTINGS_UPDATED, detail="QR automático")
    flash(
        request,
        "El QR vuelve a generarse por folio. La imagen subida se conserva "
        "por si quiere volver a usarla.",
        "ok",
    )
    return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/configuracion/qr.img")
def settings_qr_file(request: Request, db: Session = Depends(get_db)):
    """Sirve el QR subido, para verlo en la pantalla de Configuración."""
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    fila = db.execute(
        select(Setting).where(Setting.key == "qr.image_path", Setting.market.is_(None))
    ).scalar_one_or_none()
    ruta = uploads.ruta_absoluta(fila.value if fila else None)
    if ruta is None:
        return Response(status_code=404)
    return FileResponse(ruta)


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

    # La lista sigue siendo cerrada: se anade el Operador, no se acepta
    # cualquier nombre que llegue en el formulario.
    if which not in (CRED_ADMIN, CRED_MASTER, CRED_OPERATOR):
        flash(request, "Petición no válida.", "error")
    elif new_password != confirm_password:
        flash(request, "Las dos contraseñas no coinciden.", "error")
    elif len(new_password) < 8:
        flash(request, "La contraseña debe tener al menos 8 caracteres.", "error")
    else:
        # La Master Password es la unica que no tiene usuario; las otras dos si.
        set_password(
            db,
            which,
            new_password,
            username=(username.strip() or None) if which != CRED_MASTER else None,
        )
        act.log(db, act.PASSWORD_CHANGED, request=request, detail=which)
        etiquetas = {
            CRED_ADMIN: "de acceso al panel",
            CRED_MASTER: "Master Password",
            CRED_OPERATOR: "de la cuenta Operador",
        }
        flash(request, f"Contraseña {etiquetas[which]} actualizada.", "ok")

    return RedirectResponse("/configuracion", status_code=status.HTTP_303_SEE_OTHER)


# --- salud -------------------------------------------------------------------


@app.get("/salud", include_in_schema=False)
def health(db: Session = Depends(get_db)):
    """Sirve para que el servidor o Docker sepan si la aplicacion esta viva."""
    db.execute(select(func.count(Invoice.id))).scalar_one()
    return {"ok": True, "version": settings.app_version, "utc": utcnow().isoformat()}


# --- Call Center · Operador --------------------------------------------------
#
# Modulo del Operador. Misma aplicacion y misma base de datos que el panel: no
# hay un segundo backend, tal y como pedia el alcance. Lo unico que lo separa es
# el papel de la sesion, que se decide en un solo punto (require_login /
# require_operator) y no en cada plantilla.


def render_operador(
    request: Request,
    template: str,
    db: Session,
    **context,
) -> HTMLResponse:
    """Render propio del modulo.

    No reutiliza render() a proposito: aquella funcion arma el menu de
    Administracion y los datos de las credenciales, y nada de eso debe llegar a
    una pantalla que ve el Operador.
    """
    theme = theme_from(request)
    usuario = current_user(request)
    es_admin = current_role(request) == ROLE_ADMIN
    base = {
        "app_version": settings.app_version,
        "theme": theme,
        "theme_class": {"light": "", "soft": "theme-soft", "night": "theme-night"}[theme],
        "user": usuario,
        # Como se presenta quien esta atendiendo. El nombre visible es de la
        # cuenta de Operador, asi que solo se usa cuando la sesion es de
        # Operador: si el propietario entra con su sesion de Admin a revisar el
        # modulo, la cabecera tiene que decir quien es el de verdad y no
        # ponerle el nombre de otra persona.
        "operador_nombre": usuario if es_admin else cc.nombre_visible(db, usuario),
        "callcenter_logo": url_logo_callcenter(db),
        "role": current_role(request),
        "es_admin": es_admin,
        "flashes": pop_flashes(request),
        "status_labels": STATUS_LABELS,
        "money": format_amount,
        "delivery_label": delivery_label,
        "note_labels": cc.NOTE_LABELS,
    }
    base.update(context)
    return templates.TemplateResponse(request, template, base)


@app.get("/operador/acceso", response_class=HTMLResponse)
def operator_login_form(request: Request):
    # La pantalla de acceso se queda con la marca DulceAuto aunque haya un
    # logotipo propio del Call Center, y es a proposito: se sirve sin sesion, y
    # pintar ahi el archivo subido obligaria a abrir una ruta publica a la
    # carpeta de subidas. Hoy no hay ninguna, y no merece la pena estrenar una
    # por una imagen en la pantalla de login.
    if current_role(request) == ROLE_OPERATOR:
        return RedirectResponse("/operador", status_code=status.HTTP_303_SEE_OTHER)
    theme = theme_from(request)
    return templates.TemplateResponse(
        request,
        "operator_login.html",
        {
            "app_version": settings.app_version,
            "theme_class": {"light": "", "soft": "theme-soft", "night": "theme-night"}[theme],
            "error": request.session.pop("_operator_error", None),
        },
    )


@app.post("/operador/acceso")
def operator_login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if check_operator(db, username, password):
        login_session(request, username, ROLE_OPERATOR)
        act.log(
            db,
            act.OPERATOR_LOGIN,
            actor=username,
            request=request,
            detail=f"usuario {username}",
        )
        return RedirectResponse("/operador", status_code=status.HTTP_303_SEE_OTHER)

    # Igual que en el panel: no se distingue usuario inexistente de contrasena
    # incorrecta.
    act.log(db, act.OPERATOR_LOGIN_FAILED, actor=username, request=request)
    request.session["_operator_error"] = "Usuario o contraseña incorrectos."
    return RedirectResponse("/operador/acceso", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/operador/salir")
def operator_logout(request: Request, db: Session = Depends(get_db)):
    user = current_user(request)
    if user and current_role(request) == ROLE_OPERATOR:
        act.log(db, act.OPERATOR_LOGOUT, actor=user, request=request)
        logout_session(request)
    return RedirectResponse("/operador/acceso", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/operador", response_class=HTMLResponse)
def operator_panel(
    request: Request,
    folio: str = "",
    paso: int = 1,
    v: str = "",
    c: int = 0,
    necesidad: str = "",
    cat: str = "",
    q: str = "",
    db: Session = Depends(get_db),
):
    """Panel de atencion. Con folio busca la reserva; sin folio pide uno.

    El avance del guion viaja en la URL (paso, v, c, necesidad) en lugar de en
    una variable de JavaScript. Tiene dos ventajas concretas: recargar no
    devuelve al Operador al principio en mitad de una llamada, y las reglas de
    los pasos 1 y 2 se pueden comprobar aqui, en el servidor, en vez de confiar
    en que el navegador no deje pulsar un boton.
    """
    user = require_operator(request)
    if isinstance(user, RedirectResponse):
        return user

    buscado = (folio or "").strip()
    invoice = cc.buscar_por_folio(db, buscado) if buscado else None

    # Datos que el Operador declara haber verificado con quien llama.
    verificados = {p for p in (v or "").split(",") if p in cc.IDS_VERIFICABLES}
    confirmado = bool(c)
    paso = min(max(paso, 1), 6)

    # Las dos puertas del guion. Se aplican aqui y no solo en la pantalla: si
    # alguien escribe ?paso=4 a mano sin haber verificado, vuelve al paso 1.
    aviso_paso = None
    if invoice is not None and paso > 1 and len(verificados) < 2:
        paso, aviso_paso = 1, "Confirma al menos dos datos antes de continuar."
    elif invoice is not None and paso > 2 and not confirmado:
        paso, aviso_paso = 2, "Confirma que los datos principales coinciden."

    if buscado and invoice is not None:
        act.log(
            db,
            act.OPERATOR_LOOKUP,
            actor=user,
            entity_type="invoice",
            entity_id=invoice.id,
            folio=invoice.folio,
            request=request,
        )

    activas = cc.faqs_activas(db)
    todas_las_categorias = cc.categorias(activas)
    termino = (q or "").strip()
    categoria = (cat or "").strip()
    faqs = cc.filtrar_faqs(activas, termino) if termino else activas
    if categoria and categoria != "Todas":
        faqs = [f for f in faqs if f.category == categoria]

    if invoice is not None:
        nav = cc.navegacion(
            invoice, paso, verificados, confirmado, necesidad, categoria, termino
        )
        nav["categoria"] = {
            c: nav["con"](paso=4, cat=c) for c in ["Todas"] + todas_las_categorias
        }
    else:
        nav = None

    return render_operador(
        request,
        "operator.html",
        db,
        buscado=buscado,
        invoice=invoice,
        # "no encontrada" solo cuando se ha buscado algo: al entrar, la pantalla
        # no puede acusar de un error que nadie ha cometido todavia.
        no_encontrada=bool(buscado) and invoice is None,
        paso=paso,
        aviso_paso=aviso_paso,
        pasos=cc.PASOS,
        paso_actual=cc.PASOS[paso - 1],
        verificacion=cc.datos_de_verificacion(invoice) if invoice else [],
        verificados=verificados,
        verificables=cc.verificables(invoice) if invoice else 0,
        confirmado=confirmado,
        necesidad_actual=(necesidad or "").strip(),
        necesidades=cc.NECESIDADES,
        faqs=faqs,
        faq_categorias=todas_las_categorias,
        faq_cat=categoria or "Todas",
        faq_q=termino,
        faqs_pendientes=cc.faqs_pendientes(db),
        notas=cc.notas_de(db, invoice.id) if invoice else [],
        note_types=NOTE_TYPES,
        pago=cc.datos_de_pago(invoice) if invoice else None,
        nav=nav,
    )


@app.post("/operador/notas")
def operator_note_create(
    request: Request,
    folio: str = Form(...),
    tipo: str = Form(NOTE_CUSTOMER),
    nota: str = Form(""),
    paso: int = Form(6),
    db: Session = Depends(get_db),
):
    user = require_operator(request)
    if isinstance(user, RedirectResponse):
        return user

    invoice = cc.buscar_por_folio(db, folio)
    if invoice is None:
        flash(request, "No se encontró la reserva de esa nota.", "error")
        return RedirectResponse("/operador", status_code=status.HTTP_303_SEE_OTHER)

    # La nota queda firmada con el nombre visible que hubiera puesto en el
    # momento de escribirla, no con el que haya manana. Es lo mismo que ya se
    # hace con los datos bancarios de la factura: cambiar el ajuste no puede
    # reescribir lo que ya paso. Si quien escribe es el Admin revisando el
    # modulo, firma con su propio usuario.
    firma = user if current_role(request) == ROLE_ADMIN else cc.nombre_visible(db, user)

    try:
        guardada = cc.guardar_nota(db, invoice, tipo, nota, actor=firma)
    except cc.NotaInvalida as e:
        flash(request, str(e), "error")
    else:
        act.log(
            db,
            act.OPERATOR_NOTE,
            actor=user,
            entity_type="invoice",
            entity_id=invoice.id,
            folio=invoice.folio,
            detail=cc.NOTE_LABELS.get(guardada.type, guardada.type),
            request=request,
        )
        if guardada.type == NOTE_FAQ:
            flash(
                request,
                "Sugerencia registrada. Administración decidirá si se publica en la guía.",
                "ok",
            )
        else:
            flash(request, "Nota guardada.", "ok")

    destino = f"/operador?folio={invoice.folio}&paso={min(max(paso, 1), 6)}"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


# --- Administracion de la guia y de las notas --------------------------------
#
# Estas vistas son de Admin y por tanto pasan por require_login, igual que el
# resto del panel. No hace falta ninguna comprobacion de perfil aqui dentro: si
# se anadiera, habria dos respuestas a la misma pregunta.


@app.get("/guia", response_class=HTMLResponse)
def faqs_view(request: Request, editar: int = 0, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user

    faqs = cc.faqs_todas(db)
    return render(
        request,
        "faqs.html",
        db,
        active_view="faqs",
        page_title="Guía del Call Center",
        page_sub="Respuestas aprobadas que consulta el Operador. Cambiarlas no requiere desplegar nada.",
        faqs=faqs,
        resumen=cc.resumen_guia(db),
        editando=db.get(OperatorFaq, editar) if editar else None,
        sugerencias=cc.notas_todas(db, solo_pendientes=True),
    )


@app.post("/guia/guardar")
def faq_save(
    request: Request,
    faq_id: int = Form(0),
    category: str = Form(""),
    question: str = Form(""),
    answer: str = Form(""),
    active: str = Form(""),
    desde_nota: int = Form(0),
    db: Session = Depends(get_db),
):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user

    activa = active in ("1", "on", "true")
    try:
        if faq_id:
            faq = db.get(OperatorFaq, faq_id)
            if faq is None:
                flash(request, "Esa entrada de la guía ya no existe.", "error")
                return RedirectResponse("/guia", status_code=status.HTTP_303_SEE_OTHER)
            cc.editar_faq(db, faq, category, question, answer, activa)
            act.log(db, act.FAQ_UPDATED, request=request, entity_type="faq",
                    entity_id=faq.id, detail=faq.question[:80])
            flash(request, "Entrada actualizada.", "ok")
        elif desde_nota:
            nota = db.get(OperatorNote, desde_nota)
            if nota is None:
                flash(request, "Esa sugerencia ya no existe.", "error")
                return RedirectResponse("/notas", status_code=status.HTTP_303_SEE_OTHER)
            faq = cc.faq_desde_sugerencia(db, nota, category, question, answer, activa)
            act.log(db, act.FAQ_CREATED, request=request, entity_type="faq",
                    entity_id=faq.id, folio=nota.folio,
                    detail=f"desde sugerencia del folio {nota.folio}")
            flash(
                request,
                "Sugerencia convertida en entrada de la guía y marcada como atendida.",
                "ok",
            )
        else:
            faq = cc.crear_faq(db, category, question, answer, activa)
            act.log(db, act.FAQ_CREATED, request=request, entity_type="faq",
                    entity_id=faq.id, detail=faq.question[:80])
            flash(request, "Entrada añadida a la guía.", "ok")
    except cc.FaqInvalida as e:
        flash(request, str(e), "error")

    return RedirectResponse("/guia", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/guia/{faq_id}/publicar")
def faq_toggle(request: Request, faq_id: int, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user

    faq = db.get(OperatorFaq, faq_id)
    if faq is None:
        flash(request, "Esa entrada de la guía ya no existe.", "error")
    else:
        try:
            activa = cc.alternar_faq(db, faq)
        except cc.FaqInvalida as e:
            flash(request, str(e), "error")
        else:
            act.log(db, act.FAQ_UPDATED, request=request, entity_type="faq",
                    entity_id=faq.id,
                    detail="publicada" if activa else "retirada de la guía")
            flash(
                request,
                "Publicada: el Operador ya la ve." if activa
                else "Retirada: el Operador deja de verla.",
                "ok",
            )
    return RedirectResponse("/guia", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/guia/{faq_id}/eliminar")
def faq_delete(request: Request, faq_id: int, db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user

    faq = db.get(OperatorFaq, faq_id)
    if faq is None:
        flash(request, "Esa entrada de la guía ya no existe.", "error")
    else:
        # Se anota ANTES de borrar: despues, el id ya no existe y la pregunta
        # tampoco, y en Actividad quedaria una linea que no dice nada.
        act.log(db, act.FAQ_DELETED, request=request, entity_type="faq",
                entity_id=faq.id, detail=faq.question[:80])
        cc.borrar_faq(db, faq)
        flash(request, "Entrada eliminada de la guía.", "ok")
    return RedirectResponse("/guia", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/guia/{faq_id}/mover")
def faq_move(request: Request, faq_id: int, arriba: int = Form(1),
             db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user

    faq = db.get(OperatorFaq, faq_id)
    if faq is not None:
        cc.mover_faq(db, faq, bool(arriba))
    return RedirectResponse("/guia", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/notas", response_class=HTMLResponse)
def notes_view(request: Request, tipo: str = "", pendientes: int = 0,
               db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user

    filtro = tipo if tipo in NOTE_TYPES else ""
    notas = cc.notas_todas(db, tipo=filtro, solo_pendientes=bool(pendientes))
    # Se resuelven las facturas de una vez para poder enlazarlas sin hacer una
    # consulta por fila.
    ids = {n.invoice_id for n in notas}
    facturas = {
        i.id: i
        for i in db.execute(select(Invoice).where(Invoice.id.in_(ids))).scalars().all()
    } if ids else {}

    return render(
        request,
        "notes.html",
        db,
        active_view="notes",
        page_title="Notas del Call Center",
        page_sub="Lo que registran los Operadores durante las llamadas. No se editan ni se borran.",
        notas=notas,
        facturas=facturas,
        resumen=cc.resumen_notas(db),
        note_labels=cc.NOTE_LABELS,
        note_types=NOTE_TYPES,
        filtro=filtro,
        solo_pendientes=bool(pendientes),
    )


@app.post("/notas/{nota_id}/atendida")
def note_handled(request: Request, nota_id: int, atendida: int = Form(1),
                 db: Session = Depends(get_db)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user

    nota = db.get(OperatorNote, nota_id)
    if nota is None:
        flash(request, "Esa nota ya no existe.", "error")
    else:
        cc.marcar_atendida(db, nota, bool(atendida))
        act.log(db, act.NOTE_REVIEWED, request=request, entity_type="note",
                entity_id=nota.id, folio=nota.folio,
                detail="atendida" if atendida else "vuelta a pendiente")
        flash(
            request,
            "Sugerencia marcada como atendida." if atendida
            else "Sugerencia devuelta a pendientes.",
            "ok",
        )
    destino = request.headers.get("referer") or "/notas"
    if "/notas" not in destino and "/guia" not in destino:
        destino = "/notas"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)
