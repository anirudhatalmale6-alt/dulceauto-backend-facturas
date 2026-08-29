"""Tipos de documento.

Hasta el Milestone 3 una factura tenia un solo documento y la plantilla se
elegia por mercado. Desde el Milestone 4 hay tres, y la plantilla se elige por
mercado **y** tipo:

  - factura              la pre-factura de siempre, en los tres mercados
  - pago_apartado        "Pago de apartado confirmado", solo es-MX
  - documentacion        "Documentacion validada", solo es-MX

Los dos nuevos son documentos COMPLEMENTARIOS: no sustituyen a la pre-factura,
que se sigue generando exactamente igual que antes. Cada uno lleva su propio
historial de versiones (ver models.InvoiceSnapshot.doc_type), de modo que
generar uno no toca a los otros dos.

Por que un modulo aparte
------------------------
documents.py es el motor y no deberia saber cuantos documentos hay. Aqui se
declara lo que cambia de uno a otro -- archivo, ancho de diseno, elemento que
se mide al imprimir, mercados en los que existe -- y el motor lo lee.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import (
    STATUS_CANCELLED,
    STATUS_DELIVERED,
    STATUS_DRAFT,
    STATUS_PENDING,
    STATUS_SCHEDULED,
    STATUS_VALIDATED,
)

FACTURA = "factura"
PAGO_APARTADO = "pago_apartado"
DOCUMENTACION = "documentacion"


@dataclass(frozen=True)
class TipoDocumento:
    clave: str
    nombre: str
    # Nombre del archivo dentro de la carpeta del mercado. En la factura es
    # None porque cada mercado tiene el suyo con nombre propio (factura.html en
    # es-MX y es-AR, invoice.html en en) y lo dice locales.get_market().
    archivo: str | None
    # Elemento que se mide y se escala al imprimir. La factura envuelve su
    # documento en .invoice; los dos nuevos, en .page.
    raiz: str
    # Ancho en px al que esta calibrado el diseno. NO es una preferencia: es el
    # ancho con el que se dibujo la maqueta, y la impresion lo reproduce y luego
    # escala la hoja entera. Ponerlo mal no da error, da un documento con las
    # columnas estrechadas.
    ancho: int
    # Estilo del <img> del logotipo del perfil de marca. Cada documento reserva
    # un hueco de alto distinto y el motor lo inyecta en linea, asi que tiene
    # que venir de aqui y no estar escrito en el motor.
    logo_estilo: str
    # Mercados en los que existe. Los dos nuevos son es-MX en el Milestone 4;
    # las traducciones quedaron acordadas para un tuneup posterior.
    mercados: tuple[str, ...]
    # Nombre del archivo PDF que se descarga, ya con el folio delante.
    sufijo_pdf: str
    # Si se comprueba el suelo de legibilidad antes de imprimir.
    #
    # La pre-factura va en False y NO es un olvido. Hasta hoy su contrato es
    # "encoge lo que haga falta y falla solo si se parte en dos hojas", y hay
    # facturas reales que se imprimen bien por debajo de ese suelo. Activarselo
    # cambiaria el comportamiento de algo que ya funciona en produccion, y el
    # aviso lo pidio el cliente para los dos documentos nuevos, no para ella.
    comprueba_legibilidad: bool


TIPOS: dict[str, TipoDocumento] = {
    FACTURA: TipoDocumento(
        clave=FACTURA,
        nombre="Pre-factura",
        archivo=None,
        raiz=".invoice",
        ancho=900,
        logo_estilo="max-height:34px;max-width:220px",
        mercados=("es-MX", "es-AR", "en"),
        sufijo_pdf="",
        comprueba_legibilidad=False,
    ),
    PAGO_APARTADO: TipoDocumento(
        clave=PAGO_APARTADO,
        nombre="Pago de apartado confirmado",
        archivo="pago-apartado-confirmado.html",
        raiz=".page",
        ancho=1038,
        logo_estilo="max-height:52px;max-width:230px",
        mercados=("es-MX",),
        sufijo_pdf="-pago-apartado",
        comprueba_legibilidad=True,
    ),
    DOCUMENTACION: TipoDocumento(
        clave=DOCUMENTACION,
        nombre="Documentación validada",
        archivo="documentacion-validada.html",
        raiz=".page",
        ancho=1038,
        logo_estilo="max-height:58px;max-width:250px",
        mercados=("es-MX",),
        sufijo_pdf="-documentacion-validada",
        comprueba_legibilidad=True,
    ),
}

# Los complementarios, en el orden en que se ensenan en el panel.
COMPLEMENTARIOS = (PAGO_APARTADO, DOCUMENTACION)


def tipo(clave: str | None) -> TipoDocumento:
    """El tipo pedido, o la pre-factura si la clave no existe. Se cae hacia la
    factura a proposito: una clave mal escrita en una URL debe ensenar el
    documento de siempre, no reventar."""
    return TIPOS.get(clave or FACTURA, TIPOS[FACTURA])


def existe_para(clave: str, locale: str) -> bool:
    return (locale or "es-MX") in tipo(clave).mercados


# --- pareja estado -> documento ----------------------------------------------
#
# Acordado por escrito con el cliente el 29-ago-2026:
#   Pago validado      -> Pago de apartado confirmado
#   Entrega coordinada -> Documentacion validada
#
# Esto es solo el valor de partida. La pareja se guarda en Configuracion
# (claves docs.<estado>) para que el cliente pueda cambiarla sin tocar codigo,
# que fue una condicion suya.
PAREJA_POR_DEFECTO: dict[str, str] = {
    STATUS_VALIDATED: PAGO_APARTADO,
    STATUS_SCHEDULED: DOCUMENTACION,
}

AJUSTE_PAREJA = "docs.por_estado."


def clave_ajuste(estado: str) -> str:
    return AJUSTE_PAREJA + estado


# --- textos que dependen del estado ------------------------------------------
#
# El cliente eligio que los dos documentos se adapten al estado real de la
# factura en lugar de quedarse con el texto de la maqueta. La razon la dio el
# propio archivo: "Pago de apartado confirmado" lleva escrita la pastilla PAGO
# VALIDADO y una barra de progreso con "Entrega coordinada" pendiente, asi que
# emitido en Entrega coordinada le diria al cliente final que su entrega no esta
# coordinada.
#
# La frase del estado en el que cada documento se emite normalmente es LA DE LA
# MAQUETA APROBADA, palabra por palabra. Las demas se han escrito para no
# contradecir la operacion.

_PAGO_TEXTOS = {
    "doc_estado_frase": {
        STATUS_DRAFT: "Su reserva está registrada y a la espera de que se acredite el pago del apartado.",
        STATUS_PENDING: "Su reserva está registrada y a la espera de que se acredite el pago del apartado.",
        # --- la de la maqueta aprobada ---
        STATUS_VALIDATED: "Su reserva continúa en proceso y avanzamos con la documentación y coordinación de entrega de su vehículo.",
        STATUS_SCHEDULED: "Su entrega ya está coordinada y le confirmaremos la fecha y el horario acordados.",
        STATUS_DELIVERED: "Su vehículo ya ha sido entregado y la operación queda cerrada.",
        STATUS_CANCELLED: "Esta operación ha sido cancelada. Si tiene cualquier duda, su representante le atenderá.",
    },
    "doc_proxima_titulo": {
        STATUS_DRAFT: "Validación del pago",
        STATUS_PENDING: "Validación del pago",
        STATUS_VALIDATED: "Documentación y coordinación de entrega",
        STATUS_SCHEDULED: "Entrega del vehículo",
        STATUS_DELIVERED: "Operación completada",
        STATUS_CANCELLED: "Operación cancelada",
    },
    "doc_proxima_texto": {
        STATUS_DRAFT: "En cuanto recibamos el comprobante validaremos el pago y continuaremos con el proceso.",
        STATUS_PENDING: "En cuanto recibamos el comprobante validaremos el pago y continuaremos con el proceso.",
        STATUS_VALIDATED: "Nos comunicaremos con usted para confirmar los datos necesarios y continuar con el proceso.",
        STATUS_SCHEDULED: "Le confirmaremos la fecha y el horario, y realizaremos la entrega en el lugar acordado.",
        STATUS_DELIVERED: "No queda ninguna gestión pendiente. Gracias por su confianza.",
        STATUS_CANCELLED: "No hay ninguna gestión pendiente asociada a este folio.",
    },
    "doc_registro_titulo": {
        STATUS_DRAFT: "Su operación ha quedado registrada",
        STATUS_PENDING: "Su operación ha quedado registrada",
        STATUS_VALIDATED: "Su operación ha quedado registrada",
        STATUS_SCHEDULED: "Su entrega está coordinada",
        STATUS_DELIVERED: "Su operación está completada",
        STATUS_CANCELLED: "Su operación ha sido cancelada",
    },
    "doc_registro_texto": {
        STATUS_DRAFT: "Continuaremos en cuanto se acredite el pago del apartado.",
        STATUS_PENDING: "Continuaremos en cuanto se acredite el pago del apartado.",
        STATUS_VALIDATED: "Continuamos con la validación documental y los trámites correspondientes.",
        STATUS_SCHEDULED: "Continuamos con los preparativos de la entrega en el lugar y la fecha acordados.",
        STATUS_DELIVERED: "El vehículo ha sido entregado y la documentación queda cerrada.",
        STATUS_CANCELLED: "No se realizará ninguna gestión adicional sobre este folio.",
    },
}

_DOCUMENTACION_TEXTOS = {
    "doc_estado_frase": {
        STATUS_DRAFT: "En cuanto se acredite el pago del apartado continuaremos con la autorización y la entrega.",
        STATUS_PENDING: "En cuanto se acredite el pago del apartado continuaremos con la autorización y la entrega.",
        STATUS_VALIDATED: "Solo falta acreditar el pago restante para autorizar y programar la entrega de tu vehículo.",
        # --- la de la maqueta aprobada, adaptada al estado en el que se emite ---
        STATUS_SCHEDULED: "Tu entrega ya está coordinada y te confirmaremos la fecha y el horario acordados.",
        STATUS_DELIVERED: "Tu vehículo ya ha sido entregado y la operación queda cerrada.",
        STATUS_CANCELLED: "Esta operación ha sido cancelada. Si tienes cualquier duda, tu representante te atenderá.",
    },
    "doc_restante_sub": {
        STATUS_DRAFT: "Pendiente para autorizar y programar la entrega.",
        STATUS_PENDING: "Pendiente para autorizar y programar la entrega.",
        STATUS_VALIDATED: "Pendiente para autorizar y programar la entrega.",
        STATUS_SCHEDULED: "Pendiente de acreditar antes de la entrega.",
        STATUS_DELIVERED: "Operación completada.",
        STATUS_CANCELLED: "Operación cancelada.",
    },
    "doc_entrega_sub": {
        STATUS_DRAFT: "Sujeto a confirmación del pago restante.",
        STATUS_PENDING: "Sujeto a confirmación del pago restante.",
        STATUS_VALIDATED: "Sujeto a confirmación del pago restante.",
        STATUS_SCHEDULED: "Fecha y horario ya coordinados contigo.",
        STATUS_DELIVERED: "Entrega realizada.",
        STATUS_CANCELLED: "Operación cancelada.",
    },
    "doc_paso_pago": {
        STATUS_DRAFT: "Realiza el pago restante en la misma cuenta bancaria indicada en tu pre-factura.",
        STATUS_PENDING: "Realiza el pago restante en la misma cuenta bancaria indicada en tu pre-factura.",
        STATUS_VALIDATED: "Realiza el pago restante en la misma cuenta bancaria indicada en tu pre-factura.",
        STATUS_SCHEDULED: "Si aún queda pago pendiente, realízalo en la misma cuenta bancaria indicada en tu pre-factura.",
        STATUS_DELIVERED: "El pago quedó completado y la operación está cerrada.",
        STATUS_CANCELLED: "No hay ningún pago pendiente en esta operación.",
    },
}

TEXTOS_POR_ESTADO: dict[str, dict[str, dict[str, str]]] = {
    PAGO_APARTADO: _PAGO_TEXTOS,
    DOCUMENTACION: _DOCUMENTACION_TEXTOS,
}


def textos_de_estado(clave: str, estado: str) -> dict[str, str]:
    """Los huecos doc_* de ese documento para ese estado.

    Un estado desconocido se trata como "pago pendiente", que es el mismo
    criterio que ya usan locales.status_text() y documents.PROGRESO: mejor el
    texto mas conservador que un hueco vacio.
    """
    tabla = TEXTOS_POR_ESTADO.get(clave)
    if not tabla:
        return {}
    return {hueco: por_estado.get(estado, por_estado[STATUS_PENDING])
            for hueco, por_estado in tabla.items()}
