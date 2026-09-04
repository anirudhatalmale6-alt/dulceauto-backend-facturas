"""
Las seis verificaciones de la pagina 2.

Son las seis tarjetas verdes del diseno aprobado. Cada una afirma algo sobre el
vehiculo -que no tiene reporte de robo, que no arrastra adeudos- y por eso NO
son decorativas: las marca el administrador y solo salen impresas las que haya
marcado.

Por defecto no hay ninguna marcada. Esto es deliberado y va contra lo que hace
la maqueta, que las ensena las seis con su palomita. La maqueta es un dibujo; el
documento que sale de aqui se le entrega a un comprador. Que el sistema afirme
"sin reporte de robo" sobre una unidad que nadie ha consultado seria poner en
boca de DulceAuto algo que nadie ha comprobado, y ademas por escrito y con su
logotipo encima. Marcar cuesta un clic; desdecirse de un documento entregado,
no.

El texto de cada tarjeta es el del diseno, palabra por palabra. Estan
redactados con cuidado -"no se reporta", "no se detectan", "en la consulta"-
para decir lo que la consulta devolvio y no mas que eso.
"""
from __future__ import annotations

from dataclasses import dataclass

# El SVG de cada tarjeta sale del diseno aprobado. Se guarda aqui porque el
# motor tiene que poder pintar las tarjetas en cualquier orden y numero, y no
# solo las seis de la maqueta en el orden de la maqueta.
_ICONO_COCHE = (
    '<path d="M5 16h11l-1-4a2 2 0 0 0-2-1H8a2 2 0 0 0-2 1l-1 4Zm2-5 1-3h5l1 3'
    'M6 16v2m9-2v2M18 15l1 1 3-3"/>'
)
_ICONO_DOCUMENTO = '<path d="M6 3h8l4 4v14H6V3Zm8 0v5h5M9 13l2 2 4-4"/>'
_ICONO_PLACA = '<path d="M4 8h16v9H4V8Zm3 3h2m2 0h2m2 0h2M8 14h8M18 5l1 1 3-3"/>'
_ICONO_BALANZA = (
    '<path d="M6 20h12M9 17h6M12 4v11M8 6h8M5 10l3-4 3 4M13 10l3-4 3 4M5 10h6M13 10h6"/>'
)
_ICONO_RELOJ = '<path d="M4 12a8 8 0 1 0 2-5M4 4v4h4M12 8v5l3 2"/>'


@dataclass(frozen=True)
class Verificacion:
    clave: str
    titulo: str
    texto: str
    icono: str


# El orden es el del diseno y no se cambia: es el orden en que se leen las
# tarjetas en la hoja.
VERIFICACIONES: tuple[Verificacion, ...] = (
    Verificacion(
        "robo",
        "Sin reporte de robo",
        "No se reporta una alerta de robo vigente asociada al vehículo.",
        _ICONO_COCHE,
    ),
    Verificacion(
        "regularizacion",
        "Regularización conforme",
        "No se detectan registros de regularización pendientes en la consulta.",
        _ICONO_DOCUMENTO,
    ),
    Verificacion(
        "placas",
        "Placas verificadas",
        "Los registros consultados de placas no presentan inconsistencias relevantes.",
        _ICONO_PLACA,
    ),
    Verificacion(
        "adeudos",
        "Sin adeudos relevantes",
        "No se identifican adeudos fiscales o de tenencia vigentes en la consulta.",
        _ICONO_DOCUMENTO,
    ),
    Verificacion(
        "judiciales",
        "Sin alertas judiciales relevantes",
        "No se encontraron incidencias judiciales o ministeriales relevantes.",
        _ICONO_BALANZA,
    ),
    Verificacion(
        "historial",
        "Historial consultado",
        "Las fuentes consultadas no reportan incidencias relevantes para el VIN.",
        _ICONO_RELOJ,
    ),
)

POR_CLAVE = {v.clave: v for v in VERIFICACIONES}
CLAVES = tuple(v.clave for v in VERIFICACIONES)

# Cuantas tarjetas caben en una fila del panel, segun el diseno.
POR_FILA = 3


def leer(guardado: str | None) -> list[str]:
    """De lo que hay en la base de datos a la lista de claves marcadas.

    Se filtra contra CLAVES a proposito: si algun dia se quita una verificacion
    del catalogo, las facturas viejas que la tuvieran guardada dejan de
    ensenarla en vez de pintar una tarjeta sin titulo ni texto.

    El orden que se devuelve es SIEMPRE el del diseno, no el que tuviera
    guardado: si no, dos facturas con las mismas verificaciones podrian
    imprimirlas en distinto orden segun en que orden se marcaron.
    """
    if not guardado:
        return []
    marcadas = {t.strip() for t in guardado.split(",") if t.strip()}
    return [c for c in CLAVES if c in marcadas]


def guardar(claves) -> str:
    """De lo que llega del formulario a lo que se guarda."""
    pedidas = set(claves or ())
    return ",".join(c for c in CLAVES if c in pedidas)


def marcadas(invoice) -> list[Verificacion]:
    return [POR_CLAVE[c] for c in leer(getattr(invoice, "verifications", None))]


def filas(cuantas: int) -> int:
    """Cuantas filas de tarjetas hacen falta para `cuantas` verificaciones."""
    return -(-cuantas // POR_FILA) if cuantas else 0
