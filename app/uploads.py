"""
Archivos que sube el operador: logotipo y fotografias del vehiculo.

Regla de la casa: **no se cree lo que dice el archivo**. Ni la extension ni el
Content-Type que manda el navegador prueban nada, porque los pone quien sube el
archivo. Se abre la imagen y se mira lo que hay dentro; si no se puede abrir, no
entra.

Los archivos se guardan con un nombre que genera el servidor, nunca con el que
traia. Un nombre de archivo del usuario puede llevar barras, puntos dobles o
caracteres que el sistema interpreta, y colarse fuera de la carpeta.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from .config import settings

# Tamano maximo por archivo. Una fotografia de coche razonable no llega a esto,
# y el limite evita que un descuido llene el disco del servidor.
MAX_BYTES = 8 * 1024 * 1024

# Lo que se admite, con la extension que se le pondra al guardarlo.
FORMATOS = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
}

# El SVG no es una imagen que Pillow pueda abrir: es texto. Se trata aparte.
SVG_PROHIBIDO = re.compile(r"<\s*(script|foreignObject)\b|\son\w+\s*=|javascript:", re.I)


class SubidaInvalida(ValueError):
    """El archivo no vale. El mensaje se le ensena al operador tal cual."""


@dataclass
class Guardado:
    ruta: Path            # ruta absoluta en disco
    relativa: str         # lo que se guarda en la base de datos
    formato: str
    ancho: int
    alto: int


def _carpeta(sub: str) -> Path:
    destino = settings.uploads_dir / sub
    destino.mkdir(parents=True, exist_ok=True)
    return destino


def _validar_svg(datos: bytes) -> None:
    """Un SVG es codigo. Se rechaza el que traiga script o manejadores de
    eventos: acabaria dentro del documento de la factura y de su PDF."""
    texto = datos.decode("utf-8", errors="replace")
    if "<svg" not in texto.lower():
        raise SubidaInvalida("Ese archivo dice ser SVG pero no lo parece.")
    if SVG_PROHIBIDO.search(texto):
        raise SubidaInvalida(
            "Ese SVG lleva código dentro (script o eventos) y no se puede aceptar."
        )


def guardar_imagen(datos: bytes, nombre_original: str, sub: str) -> Guardado:
    """Comprueba la imagen y la guarda. Devuelve donde ha quedado."""
    if not datos:
        raise SubidaInvalida("El archivo está vacío.")
    if len(datos) > MAX_BYTES:
        raise SubidaInvalida(
            f"El archivo pesa {len(datos) / 1024 / 1024:.1f} MB y el máximo es "
            f"{MAX_BYTES // 1024 // 1024} MB."
        )

    es_svg = nombre_original.lower().endswith(".svg") or datos.lstrip()[:5].lower() in (
        b"<?xml",
        b"<svg",
    )

    if es_svg:
        _validar_svg(datos)
        formato, extension, ancho, alto = "SVG", ".svg", 0, 0
    else:
        from PIL import Image, UnidentifiedImageError
        import io

        try:
            with Image.open(io.BytesIO(datos)) as imagen:
                imagen.verify()          # detecta archivos corruptos
            with Image.open(io.BytesIO(datos)) as imagen:
                formato = (imagen.format or "").upper()
                ancho, alto = imagen.size
        except (UnidentifiedImageError, OSError) as exc:
            raise SubidaInvalida(
                "Ese archivo no es una imagen que se pueda abrir."
            ) from exc

        if formato not in FORMATOS:
            raise SubidaInvalida(
                f"El formato {formato or 'desconocido'} no se admite. Use JPG, PNG, WEBP o SVG."
            )
        extension = FORMATOS[formato]

    # Nombre generado aqui: el que traiga el archivo no se usa nunca.
    destino = _carpeta(sub) / f"{secrets.token_hex(8)}{extension}"
    destino.write_bytes(datos)
    return Guardado(
        ruta=destino,
        relativa=str(destino.relative_to(settings.data_dir)),
        formato=formato,
        ancho=ancho,
        alto=alto,
    )


def ruta_absoluta(relativa: str | None) -> Path | None:
    """Ruta en disco de un archivo guardado, o None si ya no esta.

    Se comprueba ademas que quede dentro de la carpeta de datos: una ruta con
    ".." en la base apuntaria a cualquier archivo del servidor.
    """
    if not relativa:
        return None
    ruta = (settings.data_dir / relativa).resolve()
    if not ruta.is_relative_to(settings.data_dir.resolve()):
        return None
    return ruta if ruta.exists() else None


def borrar(relativa: str | None) -> None:
    ruta = ruta_absoluta(relativa)
    if ruta is not None:
        ruta.unlink(missing_ok=True)
