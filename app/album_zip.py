"""
Carga del album desde un archivo ZIP.

Un ZIP que sube alguien de fuera es un archivo hostil hasta que se demuestre lo
contrario. Todo lo que dice de si mismo -los nombres de sus entradas, los
tamanos que declara- lo escribio quien lo creo, asi que aqui no se cree nada de
eso: se comprueba.

Las tres cosas de las que hay que defenderse, y como:

  1. Rutas que salen de la carpeta. Una entrada llamada "../../etc/passwd" o
     "/etc/passwd" escribiria fuera del sitio previsto. Aqui NO se extrae a
     disco por ruta: se lee el contenido en memoria y lo guarda uploads.py, que
     genera el nombre del archivo el mismo. Aun asi las entradas con rutas raras
     se rechazan antes, porque un ZIP que las trae no es un ZIP de fotos.

  2. ZIP bomba. Un archivo de 2 MB puede descomprimirse en varios gigas y
     llenar el disco. Se limita el total descomprimido, y -esto es lo que de
     verdad protege- se limita MIENTRAS se lee, no fiandose del tamano que la
     entrada declara: ese numero es parte del archivo y puede mentir.

  3. Contenido que no es lo que dice. La extension no prueba nada. Cada imagen
     pasa por uploads.guardar_imagen, que la abre de verdad con Pillow; si no
     se puede abrir o no es JPG/PNG/WebP, no entra.

Sustituir el ZIP reemplaza el album ENTERO. Es lo que pidio el cliente y ademas
es lo unico coherente: si se mezclaran las fotos de dos cargas, el album
quedaria con la foto 1 de una y la 7 de otra, y nadie sabria por que.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field

from . import uploads
from .album import MAX_FOTOS

# Total descomprimido que se admite en un ZIP. Veinte fotografias de coche no
# llegan ni de lejos; el limite esta para que un archivo preparado no llene el
# disco del servidor.
MAX_TOTAL_BYTES = 80 * 1024 * 1024

# Numero de entradas que se miran. Un ZIP con cien mil entradas vacias tarda mas
# en recorrerse que en subirse; se corta mucho antes de eso.
MAX_ENTRADAS = 500

# Formatos que se aceptan dentro del ZIP. Es un subconjunto de los que admite
# uploads.py: el SVG vale para un logotipo, pero no es una fotografia de un
# coche y no tiene sentido en el album.
EXTENSIONES = (".jpg", ".jpeg", ".png", ".webp")

# Basura que meten los sistemas al comprimir una carpeta. No es un error del
# usuario, asi que se salta sin decir nada en vez de contarla como rechazada.
def _es_basura(nombre: str) -> bool:
    partes = nombre.replace("\\", "/").split("/")
    return any(
        p.startswith(".") or p == "__MACOSX" or p.lower() == "thumbs.db"
        for p in partes
        if p
    )


class ZipInvalido(ValueError):
    """El ZIP entero no sirve. El mensaje se le ensena al operador tal cual."""


@dataclass
class Resultado:
    imagenes: list[tuple[str, bytes]] = field(default_factory=list)
    descartadas: list[str] = field(default_factory=list)
    sobrantes: int = 0          # cuantas se dejaron fuera por pasar de MAX_FOTOS

    @property
    def cuantas(self) -> int:
        return len(self.imagenes)


_TROZOS = re.compile(r"(\d+)")


def clave_natural(nombre: str) -> tuple:
    """Orden natural: foto2 antes que foto10.

    Con el orden alfabetico de toda la vida, 'foto10' va antes que 'foto2'
    porque compara caracter a caracter. El vendedor numera las fotos y espera
    que la 2 salga la segunda, no la decima.
    """
    partes = _TROZOS.split(nombre.lower())
    return tuple((1, int(p)) if p.isdigit() else (0, p) for p in partes)


def _ruta_sospechosa(nombre: str) -> bool:
    """Nombres que no pueden venir de una carpeta normal de fotografias."""
    limpio = nombre.replace("\\", "/")
    if limpio.startswith("/") or re.match(r"^[a-zA-Z]:", limpio):
        return True          # ruta absoluta
    if ".." in limpio.split("/"):
        return True          # sube de carpeta
    if "\x00" in nombre:
        return True
    return False


def _leer_acotado(zf: zipfile.ZipFile, info: zipfile.ZipInfo, disponible: int) -> bytes:
    """Lee una entrada sin pasar de `disponible` bytes.

    Se lee de una en una, en trozos, y se corta en cuanto se pasa. NO se mira
    info.file_size para decidir: ese numero esta escrito dentro del propio ZIP y
    quien lo prepara puede poner lo que quiera. El unico limite que protege es
    el que se aplica sobre los bytes que van saliendo.
    """
    trozos, leidos = [], 0
    with zf.open(info) as entrada:
        while True:
            trozo = entrada.read(64 * 1024)
            if not trozo:
                break
            leidos += len(trozo)
            if leidos > disponible:
                raise ZipInvalido(
                    "El contenido del ZIP descomprimido pasa de "
                    f"{MAX_TOTAL_BYTES // 1024 // 1024} MB. No se ha guardado nada."
                )
            trozos.append(trozo)
    return b"".join(trozos)


def leer(datos: bytes) -> Resultado:
    """Saca del ZIP las fotografias, en orden natural de nombre.

    Devuelve los bytes, no archivos en disco: guardarlos es cosa de uploads.py,
    que es quien decide el nombre. Asi ninguna ruta del ZIP llega nunca al
    sistema de archivos.
    """
    if not datos:
        raise ZipInvalido("El archivo está vacío.")

    try:
        zf = zipfile.ZipFile(io.BytesIO(datos))
    except zipfile.BadZipFile as exc:
        raise ZipInvalido("Ese archivo no es un ZIP que se pueda abrir.") from exc

    with zf:
        if zf.testzip() is not None:
            raise ZipInvalido("El ZIP está dañado: alguna de sus entradas no se puede leer.")

        entradas = zf.infolist()
        if len(entradas) > MAX_ENTRADAS:
            raise ZipInvalido(
                f"El ZIP trae {len(entradas)} entradas y el máximo son {MAX_ENTRADAS}."
            )

        candidatas: list[zipfile.ZipInfo] = []
        resultado = Resultado()

        for info in entradas:
            nombre = info.filename
            if info.is_dir() or _es_basura(nombre):
                continue
            if _ruta_sospechosa(nombre):
                raise ZipInvalido(
                    f"El ZIP trae una entrada con una ruta que no es válida ({nombre!r}). "
                    "No se ha guardado nada."
                )
            if not nombre.lower().endswith(EXTENSIONES):
                resultado.descartadas.append(nombre)
                continue
            candidatas.append(info)

        if not candidatas:
            raise ZipInvalido(
                "El ZIP no trae ninguna imagen JPG, PNG o WebP."
            )

        # El orden se decide con el nombre SIN carpetas: un ZIP con las fotos
        # dentro de una carpeta ordenaria por la carpeta y no por la foto.
        candidatas.sort(key=lambda i: clave_natural(i.filename.replace("\\", "/").split("/")[-1]))

        # Las que pasen de MAX_FOTOS ni se leen: no tiene sentido descomprimir
        # lo que se va a tirar.
        if len(candidatas) > MAX_FOTOS:
            resultado.sobrantes = len(candidatas) - MAX_FOTOS
            candidatas = candidatas[:MAX_FOTOS]

        disponible = MAX_TOTAL_BYTES
        for info in candidatas:
            contenido = _leer_acotado(zf, info, disponible)
            disponible -= len(contenido)
            nombre_corto = info.filename.replace("\\", "/").split("/")[-1]
            resultado.imagenes.append((nombre_corto, contenido))

    return resultado


def revisar(resultado: Resultado) -> tuple[list[tuple[str, bytes]], list[str]]:
    """Abre TODAS las imagenes antes de guardar ninguna.

    Se separa del guardado a proposito. Si se fuera guardando sobre la marcha,
    un ZIP con la fotografia 18 corrupta dejaria el album a medias: diecisiete
    fotos nuevas y las de la carga anterior a partir de ahi, que es justo la
    mezcla que el cliente pidio evitar.
    """
    validas, errores = [], []
    for nombre, contenido in resultado.imagenes:
        try:
            uploads.comprobar_imagen(contenido)
            validas.append((nombre, contenido))
        except uploads.SubidaInvalida as exc:
            errores.append(f"{nombre}: {exc}")
    return validas, errores
