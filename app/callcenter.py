"""
Reglas del modulo de Call Center.

Todo lo que decide algo vive aqui y no en las rutas, igual que invoices.py
guarda las reglas de facturacion. Las rutas de main.py solo traducen peticiones
HTTP a estas funciones.

Dos ideas gobiernan el modulo entero:

1. El Operador NO escribe en la factura. Puede leerla y puede anadir notas.
   Ninguna funcion de este archivo modifica una columna de Invoice, y esa es la
   garantia real de que el perfil es de solo lectura: no depende de que la
   pantalla no ofrezca el boton.

2. Lo que el Operador ve del pago sale de la propia factura (las columnas
   banking_*, que se congelaron al crearla), nunca de Configuracion. Si manana
   cambia la cuenta del negocio, el Operador seguira leyendo al cliente los
   datos que figuran en SU documento, que es lo unico que el cliente tiene
   delante.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .invoices import folio_ancho, folio_prefijo
from .models import (
    NOTE_FAQ,
    NOTE_TYPES,
    Invoice,
    OperatorFaq,
    OperatorNote,
    utcnow,
)

# Etiquetas de los tipos de nota, con los nombres del prototipo V1.4.
NOTE_LABELS = {
    "cliente": "Observación del cliente",
    "seguimiento": "Seguimiento necesario",
    NOTE_FAQ: "Sugerencia de nueva FAQ",
}

MAX_NOTA = 2000

# Los seis pasos del flujo guiado, con los textos que escribio el cliente en
# DulceAuto_CallCenter_Operador_V1.4.html. Se copian literalmente: son el guion
# que quiere que lea su gente, no un texto que me toque redactar a mi.
PASOS = [
    {
        "numero": 1,
        "nombre": "Identificar al cliente",
        "icono": "👤",
        "say": "Gracias por comunicarte con DulceAuto. Con gusto reviso tu pre-reserva. "
        "Para localizarla y validar que estoy viendo la operación correcta, ¿me "
        "compartes tu nombre completo y el folio que aparece en la parte superior "
        "del documento?",
        "checks": [
            "Confirmar nombre completo",
            "Confirmar folio",
            "Confirmar últimos 4 dígitos del teléfono",
        ],
    },
    {
        "numero": 2,
        "nombre": "Confirmar la factura",
        "icono": "📄",
        "say": "Perfecto, ya localicé tu pre-factura. Antes de continuar, quiero "
        "confirmar contigo el vehículo, el monto de la reserva y la modalidad de "
        "entrega para asegurarnos de que todo coincide.",
        "checks": [
            "Vehículo correcto",
            "Monto de reserva correcto",
            "Fecha y modalidad de entrega",
            "Sin inconsistencias",
        ],
    },
    {
        "numero": 3,
        "nombre": "Identificar la necesidad",
        "icono": "🎯",
        "say": "¿En qué puedo ayudarte hoy? Puede ser sobre el pago, la entrega, la "
        "documentación o cualquier otra duda de tu reserva.",
        "checks": [
            "Elegir un motivo principal",
            "No asumir lo que necesita el cliente",
            "Registrar nota si surge algo no contemplado",
        ],
    },
    {
        "numero": 4,
        "nombre": "Resolver dudas",
        "icono": "❓",
        "say": "Claro, con gusto. Voy a revisar la información de tu reserva y te "
        "explico el punto correspondiente para que tengas claridad y tranquilidad "
        "sobre el siguiente paso.",
        "checks": [
            "Usar respuesta aprobada",
            "Verificar el dato concreto de la reserva",
            "Mantener tono amable y claro",
            "Preguntar si quedó claro",
        ],
    },
    {
        "numero": 5,
        "nombre": "Confirmar decisión",
        "icono": "✓",
        "say": "Perfecto. Antes de terminar, quiero confirmar contigo qué deseas "
        "hacer para que el siguiente paso de tu reserva quede completamente claro.",
        "checks": [
            "Continuará",
            "Necesita más tiempo",
            "Requiere seguimiento",
            "No continuará",
        ],
    },
    {
        "numero": 6,
        "nombre": "Registrar nota",
        "icono": "📋",
        "say": "Voy a dejar una nota breve para que, si necesitas seguimiento, "
        "tengamos el contexto y no tengas que volver a explicar lo mismo.",
        "checks": [
            "Registrar solo si aporta valor",
            "Nota breve y objetiva",
            "Sugerir una FAQ si apareció una pregunta nueva",
        ],
    },
]

# Motivos del paso 3, tambien del prototipo.
NECESIDADES = [
    {
        "id": "faq",
        "icono": "❓",
        "titulo": "Resolver una duda",
        "desc": "Proceso, garantía, documentos o preguntas frecuentes.",
    },
    {
        "id": "delivery",
        "icono": "🚚",
        "titulo": "Entrega",
        "desc": "Fecha, modalidad, dirección o reprogramación.",
    },
    {
        "id": "payment",
        "icono": "🏦",
        "titulo": "Pago",
        "desc": "Monto, referencia, CLABE y orientación para transferir.",
    },
    {
        "id": "update",
        "icono": "📄",
        "titulo": "Actualizar información",
        "desc": "Registrar una solicitud de cambio para revisión.",
    },
    {
        "id": "incident",
        "icono": "🛡",
        "titulo": "Problema / incidencia",
        "desc": "Factura, pago, entrega o situación especial.",
    },
    {
        "id": "other",
        "icono": "•••",
        "titulo": "Otra necesidad",
        "desc": "Atención no contemplada en la guía.",
    },
]


class NotaInvalida(ValueError):
    """La nota no se puede guardar. El mensaje es para el Operador."""


# --- busqueda por folio ------------------------------------------------------


def normalizar_folio(db: Session, texto: str) -> str:
    """Deja el folio tal y como se guarda, o devuelve "" si no hay nada util.

    Se acepta el folio entero (RES-90001), solo el numero (90001) y cualquier
    combinacion de mayusculas o espacios, porque el Operador lo esta copiando
    de lo que le dicta un cliente por telefono. Lo que NO se hace es buscar
    "parecidos": el alcance pide busqueda exacta, y ensenar la reserva de otra
    persona porque el folio se parece seria mucho peor que no encontrarla.
    """
    escrito = (texto or "").strip().upper()
    if not escrito:
        return ""

    prefijo = folio_prefijo(db).upper()
    cuerpo = escrito
    if cuerpo.startswith(prefijo):
        cuerpo = cuerpo[len(prefijo):]
    # Un folio dictado por telefono llega con espacios o guiones sueltos.
    cuerpo = cuerpo.replace(" ", "").replace("-", "").strip()

    if cuerpo.isdigit():
        return f"{folio_prefijo(db)}{int(cuerpo):0{folio_ancho(db)}d}"
    # No encaja con el formato configurado: se busca literalmente lo escrito,
    # por si en la base hay folios historicos con otra forma.
    return escrito


def buscar_por_folio(db: Session, texto: str) -> Invoice | None:
    folio = normalizar_folio(db, texto)
    if not folio:
        return None
    return db.execute(select(Invoice).where(Invoice.folio == folio)).scalar_one_or_none()


# --- verificacion de identidad (paso 1) --------------------------------------


def ultimos4(telefono: str | None) -> str:
    """Los cuatro ultimos digitos del telefono, ignorando espacios y signos.

    Se muestran solo cuatro a proposito: bastan para verificar que quien llama
    es el titular y no obligan a tener el numero completo en pantalla durante
    toda la llamada.
    """
    digitos = "".join(c for c in (telefono or "") if c.isdigit())
    return digitos[-4:] if len(digitos) >= 4 else ""


def datos_de_verificacion(invoice: Invoice) -> list[dict]:
    """Los tres datos con los que el Operador identifica a quien llama.

    Se devuelven siempre los tres, con 'disponible' a False cuando la factura
    no tiene ese dato: el paso 1 exige confirmar dos, y el Operador tiene que
    ver cual falta en vez de encontrarse una tarjeta menos sin explicacion.
    """
    telefono = ultimos4(invoice.customer_phone)
    return [
        {
            "id": "name",
            "titulo": "Nombre completo",
            "valor": invoice.customer_name or "",
            "disponible": bool(invoice.customer_name),
        },
        {
            "id": "folio",
            "titulo": "Folio",
            "valor": invoice.folio or "",
            "disponible": bool(invoice.folio),
        },
        {
            "id": "phone",
            "titulo": "Últimos 4 dígitos",
            "valor": telefono,
            "disponible": bool(telefono),
        },
    ]


def verificables(invoice: Invoice) -> int:
    return sum(1 for d in datos_de_verificacion(invoice) if d["disponible"])


IDS_VERIFICABLES = ("name", "folio", "phone")


# --- enlaces del guion -------------------------------------------------------


def enlace(
    folio: str,
    paso: int,
    verificados,
    confirmado: bool,
    necesidad: str = "",
    cat: str = "",
    q: str = "",
) -> str:
    """Construye una URL del panel con todo el estado del guion.

    Se arma aqui, en Python, y no dentro de la plantilla. Componer una URL a
    base de trozos en Jinja obliga a partir la linea, y cada salto de linea
    acaba dentro del href: el enlace sigue "funcionando" en un navegador
    tolerante y se rompe en cualquier comprobacion automatica. Ademas asi el
    escapado lo hace urlencode una sola vez y en un solo sitio.
    """
    from urllib.parse import urlencode

    datos = {
        "folio": folio,
        "paso": paso,
        "v": ",".join(o for o in IDS_VERIFICABLES if o in set(verificados)),
        "c": 1 if confirmado else 0,
    }
    if necesidad:
        datos["necesidad"] = necesidad
    if cat and cat != "Todas":
        datos["cat"] = cat
    if q:
        datos["q"] = q
    return "/operador?" + urlencode(datos)


def navegacion(
    invoice: Invoice,
    paso: int,
    verificados: set,
    confirmado: bool,
    necesidad: str,
    cat: str,
    q: str,
) -> dict:
    """Todos los enlaces que necesita la pantalla, ya montados.

    'toggle' lleva, para cada dato verificable, la URL que lo marca si esta sin
    marcar y lo desmarca si ya lo estaba.
    """

    def con(**cambios) -> str:
        base = {
            "folio": invoice.folio,
            "paso": paso,
            "verificados": verificados,
            "confirmado": confirmado,
            "necesidad": necesidad,
            "cat": cat,
            "q": q,
        }
        base.update(cambios)
        return enlace(**base)

    toggle = {}
    for ident in IDS_VERIFICABLES:
        nuevos = set(verificados) ^ {ident}
        toggle[ident] = con(paso=1, verificados=nuevos)

    return {
        "paso": {n: con(paso=n) for n in range(1, 7)},
        "toggle": toggle,
        "confirmar": con(paso=3, confirmado=True),
        "necesidad": {
            n["id"]: con(paso=4 if n["id"] == "faq" else 3, necesidad=n["id"])
            for n in NECESIDADES
        },
        "categoria": {},  # se rellena en la vista con las categorias reales
        "con": con,
    }


# --- datos de pago congelados ------------------------------------------------


def datos_de_pago(invoice: Invoice) -> dict:
    """Lo que el Operador puede leerle al cliente para que pague.

    Sale de las columnas banking_* de ESTA factura, que se copiaron al crearla.
    No se consulta Configuracion en ningun momento: si el negocio cambia de
    cuenta manana, el cliente sigue teniendo delante su documento con la cuenta
    antigua, y lo que el Operador le lea tiene que coincidir con ese papel.
    """
    return {
        "banco": invoice.banking_bank or "",
        "beneficiario": invoice.banking_beneficiary or "",
        "etiqueta_cuenta": invoice.banking_account_label or "Cuenta",
        "cuenta": invoice.banking_account_number or "",
        # La referencia es el propio folio; se decidio asi en la Fase C.
        "referencia": invoice.banking_payment_reference or invoice.folio or "",
        "completos": bool(invoice.banking_bank and invoice.banking_account_number),
    }


def filtrar_faqs(faqs: list[OperatorFaq], termino: str) -> list[OperatorFaq]:
    """Busqueda simple sobre pregunta y respuesta.

    Se hace en Python y no en SQL a proposito: son unas decenas de filas, y asi
    la comparacion ignora mayusculas y acentos igual en sqlite que en cualquier
    otro motor. LIKE no se comporta igual en los tres, y esto tiene que seguir
    funcionando si manana se migra a MySQL o PostgreSQL.
    """
    buscado = _plano(termino)
    if not buscado:
        return faqs
    return [f for f in faqs if buscado in _plano(f.question + " " + (f.answer or ""))]


def _plano(texto: str) -> str:
    """Minusculas y sin acentos, para que 'garantia' encuentre 'garantía'."""
    import unicodedata

    limpio = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in limpio if unicodedata.category(c) != "Mn")


# --- guia de respuestas ------------------------------------------------------


def faqs_activas(db: Session) -> list[OperatorFaq]:
    """Lo que ve el Operador: solo las aprobadas.

    Se filtra ademas por respuesta no vacia. Con el CRUD del Admin bien hecho
    las dos condiciones son la misma, pero si alguna vez se marcara activa una
    pregunta sin respuesta, el Operador no debe verla: el alcance dice que no se
    improvisa, y una FAQ activa y vacia es justo una invitacion a improvisar.
    """
    filas = db.execute(
        select(OperatorFaq)
        .where(OperatorFaq.active.is_(True))
        .order_by(OperatorFaq.sort_order, OperatorFaq.id)
    ).scalars().all()
    return [f for f in filas if (f.answer or "").strip()]


def faqs_pendientes(db: Session) -> list[OperatorFaq]:
    """Preguntas recogidas sin respuesta aprobada todavia.

    El Operador las ve como pendientes y puede sugerir una redaccion, pero
    nunca como respuesta que leer al cliente.
    """
    return db.execute(
        select(OperatorFaq)
        .where(OperatorFaq.active.is_(False))
        .order_by(OperatorFaq.sort_order, OperatorFaq.id)
    ).scalars().all()


def categorias(faqs: list[OperatorFaq]) -> list[str]:
    """Categorias presentes, en el orden en que aparecen."""
    vistas: list[str] = []
    for f in faqs:
        if f.category and f.category not in vistas:
            vistas.append(f.category)
    return vistas


# --- notas -------------------------------------------------------------------


def notas_de(db: Session, invoice_id: int) -> list[OperatorNote]:
    return db.execute(
        select(OperatorNote)
        .where(OperatorNote.invoice_id == invoice_id)
        .order_by(OperatorNote.created_at.desc(), OperatorNote.id.desc())
    ).scalars().all()


def guardar_nota(
    db: Session, invoice: Invoice, tipo: str, texto: str, actor: str = "Operador"
) -> OperatorNote:
    """Anade una nota a la factura. No modifica la factura.

    Se valida el tipo contra la lista cerrada en vez de guardar lo que llegue:
    un tipo inventado desde un formulario manipulado convertiria el filtro de
    sugerencias FAQ del Admin en algo que se salta escribiendo otra palabra.
    """
    if tipo not in NOTE_TYPES:
        raise NotaInvalida("Tipo de nota no válido.")

    limpio = (texto or "").strip()
    if not limpio:
        raise NotaInvalida("Escribe el contenido de la nota.")
    if len(limpio) > MAX_NOTA:
        raise NotaInvalida(f"La nota no puede pasar de {MAX_NOTA} caracteres.")

    nota = OperatorNote(
        invoice_id=invoice.id,
        folio=invoice.folio,
        type=tipo,
        note=limpio,
        actor=actor or "Operador",
        created_at=utcnow(),
    )
    db.add(nota)
    db.commit()
    db.refresh(nota)
    return nota


def sugerencias_faq_pendientes(db: Session) -> list[OperatorNote]:
    """Sugerencias que el Admin todavia no ha atendido.

    Existen para que el Admin sepa que hay algo que revisar. Nunca se publican
    solas: convertir una sugerencia en FAQ es una accion suya, no un efecto de
    haberla escrito.
    """
    return db.execute(
        select(OperatorNote)
        .where(OperatorNote.type == NOTE_FAQ, OperatorNote.handled_at.is_(None))
        .order_by(OperatorNote.created_at.desc())
    ).scalars().all()
