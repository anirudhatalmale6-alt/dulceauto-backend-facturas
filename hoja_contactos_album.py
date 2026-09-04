"""
Hoja de contactos de la rejilla del album.

Renderiza en Chromium los 20 casos -de 1 a 20 fotografias- con el ancho real
que el album tiene dentro de la hoja A4, y los monta en dos laminas para que se
puedan ver de un vistazo y comparar unos con otros.

No dibuja nada aparte: el HTML de cada caso sale de app/album.py, que es el
mismo modulo que va a generar la pagina 2 del documento. Lo que se aprueba
mirando estas laminas es exactamente lo que se imprime.

    python hoja_contactos_album.py [directorio_de_salida]
"""
import base64
import mimetypes
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from app import album  # noqa: E402

AQUI = Path(__file__).parent
# Directorio con las fotografias de ejemplo. Se puede cambiar con la variable
# de entorno ALBUM_FOTOS.
FOTOS = Path(os.environ.get("ALBUM_FOTOS") or (AQUI / "fotos-album"))
SALIDA = Path(sys.argv[1]) if len(sys.argv) > 1 else AQUI / "hoja-contactos"
ESCALA = 2  # se renderiza al doble y se reduce, para que no salga pixelado


def fotos_en_linea() -> list[str]:
    """Las fotografias, ya metidas en la propia pagina. Se usan las del diseno
    que mando el cliente: son sus coches, asi reconoce lo que esta viendo."""
    urls = []
    for ruta in sorted(FOTOS.iterdir()) if FOTOS.is_dir() else []:
        tipo = mimetypes.guess_type(ruta.name)[0] or ""
        if tipo.startswith("image/"):
            urls.append(f"data:{tipo};base64,{base64.b64encode(ruta.read_bytes()).decode()}")
    if not urls:
        raise SystemExit(f"No hay fotografias en {FOTOS}")
    return urls


def pagina(urls: list[str]) -> str:
    """Una pagina con los 20 albumes, uno detras de otro, cada uno con el ancho
    real que tiene dentro de la hoja A4."""
    bloques = []
    for n in range(1, album.MAX_FOTOS + 1):
        reparto = album.repartir(n)
        # Las fotografias se van repitiendo en circulo: hay 14 del diseno
        # original y hacen falta hasta 20. El numero de la esquina dice siempre
        # que posicion ocupa, que es lo que importa aqui.
        marcado = album.marcado(reparto, lambda i: urls[(i - 1) % len(urls)])
        bloques.append(f'<div class="caso" id="caso{n}">{marcado}</div>')

    return f"""<!doctype html><html lang="es-MX"><head><meta charset="utf-8">
<style>
*{{box-sizing:border-box}}
html,body{{margin:0;padding:0;background:#fff;font-family:Arial,Helvetica,sans-serif}}
.caso{{width:149mm;margin:0 0 8mm}}
{album.CSS}
</style></head><body>{''.join(bloques)}</body></html>"""


def capturar() -> dict[int, Path]:
    SALIDA.mkdir(parents=True, exist_ok=True)
    crudo = SALIDA / "casos"
    crudo.mkdir(exist_ok=True)
    urls = fotos_en_linea()
    html = pagina(urls)
    (SALIDA / "casos.html").write_text(html, encoding="utf-8")

    capturas = {}
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        # El viewport se queda pequeno a proposito: lo que se captura es el
        # elemento, no la ventana, asi que no hay riesgo de sacar una imagen
        # gigante sin darse cuenta.
        pag = navegador.new_page(
            viewport={"width": 800, "height": 900}, device_scale_factor=ESCALA
        )
        pag.goto((SALIDA / "casos.html").as_uri())
        pag.wait_for_load_state("networkidle")
        for n in range(1, album.MAX_FOTOS + 1):
            destino = crudo / f"caso-{n:02d}.png"
            pag.locator(f"#caso{n} .album").screenshot(path=str(destino))
            capturas[n] = destino
        navegador.close()
    return capturas


def fuente(tam: int):
    for ruta in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(ruta).exists():
            return ImageFont.truetype(ruta, tam)
    return ImageFont.load_default()


def lamina(capturas: dict[int, Path], casos: list[int], titulo: str, destino: Path) -> Path:
    """Monta una lamina con varios casos en cinco columnas."""
    COLUMNAS = 5
    ANCHO_CELDA = 366
    MARGEN, HUECO, ALTO_ETIQUETA = 16, 14, 46
    CABECERA = 52

    muestra = Image.open(capturas[casos[0]])
    alto_celda = round(ANCHO_CELDA * muestra.height / muestra.width)
    filas = -(-len(casos) // COLUMNAS)

    ancho = MARGEN * 2 + COLUMNAS * ANCHO_CELDA + (COLUMNAS - 1) * HUECO
    alto = CABECERA + MARGEN + filas * (alto_celda + ALTO_ETIQUETA) + (filas - 1) * HUECO + MARGEN

    hoja = Image.new("RGB", (ancho, alto), "#ffffff")
    dib = ImageDraw.Draw(hoja)
    dib.rectangle([0, 0, ancho, CABECERA], fill="#0b2d56")
    dib.text((MARGEN, 17), titulo, font=fuente(20), fill="#ffffff")

    for i, n in enumerate(casos):
        col, fil = i % COLUMNAS, i // COLUMNAS
        x = MARGEN + col * (ANCHO_CELDA + HUECO)
        y = CABECERA + MARGEN + fil * (alto_celda + ALTO_ETIQUETA + HUECO)
        img = Image.open(capturas[n]).convert("RGB").resize(
            (ANCHO_CELDA, alto_celda), Image.LANCZOS
        )
        hoja.paste(img, (x, y))
        dib.rectangle([x, y, x + ANCHO_CELDA - 1, y + alto_celda - 1], outline="#c8d2de")
        reparto = album.repartir(n)
        etiqueta = f"{n} fotografía" + ("" if n == 1 else "s")
        # Cada etiqueta en su linea. Antes iban las dos en la misma, separadas
        # con espacios, y como la fuente es de ancho variable se pisaban.
        dib.text((x + 2, y + alto_celda + 5), etiqueta, font=fuente(15), fill="#0b1f3a")
        dib.text(
            (x + 2, y + alto_celda + 25),
            reparto.resumen(),
            font=fuente(12),
            fill="#6b7a8d",
        )

    hoja.save(destino, quality=92)
    print(f"  {destino.name}  {hoja.width}x{hoja.height}px")
    if hoja.width > 2000 or hoja.height > 2000:
        raise SystemExit("La lamina se ha pasado de 2000px; hay que reducir la celda.")
    return destino


def main() -> None:
    print("Renderizando los 20 casos en Chromium...")
    capturas = capturar()
    muestra = Image.open(capturas[20])
    print(f"  cada caso: {muestra.width}x{muestra.height}px a escala {ESCALA}")
    print("Montando las laminas...")
    lamina(
        capturas,
        list(range(1, 11)),
        "DulceAuto · Álbum de la página 2 · casos de 1 a 10 fotografías",
        SALIDA / "hoja-contactos-1-10.jpg",
    )
    lamina(
        capturas,
        list(range(11, 21)),
        "DulceAuto · Álbum de la página 2 · casos de 11 a 20 fotografías",
        SALIDA / "hoja-contactos-11-20.jpg",
    )
    print("Listo.")


if __name__ == "__main__":
    main()
