"""
Que le pasa a la rejilla escrita a mano cuando el numero de fotografias no es
exactamente 14.

Es la imagen que acompana al control positivo de verificar_album.py: alli el
hueco y el desbordamiento salen como numeros, y aqui se ven.

    python porque_una_regla.py
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from app import album  # noqa: E402
from comparativa_album_14 import CSS_ORIGINAL  # noqa: E402
from hoja_contactos_album import ESCALA, SALIDA, fotos_en_linea, fuente  # noqa: E402

CASOS = (13, 15)


def main() -> None:
    urls = fotos_en_linea()

    def viejo(cuantas: int) -> str:
        figuras = []
        for i in range(1, cuantas + 1):
            clase = "main" if i == 1 else f"p{i}"
            figuras.append(
                f'<figure class="photo {clase}"><img src="{urls[(i - 1) % len(urls)]}" '
                f'alt="Foto {i}"><figcaption>{i:02d}</figcaption></figure>'
            )
        return '<div class="album-orig">' + "".join(figuras) + "</div>"

    partes = []
    for n in CASOS:
        partes.append(f'<div class="caso" id="viejo{n}">{viejo(n)}</div>')
        partes.append(
            f'<div class="caso" id="nuevo{n}">'
            + album.marcado(album.repartir(n), lambda i: urls[(i - 1) % len(urls)])
            + "</div>"
        )

    # El contenedor mide justo el hueco que la hoja reserva al album: 136mm.
    # Lo que no quepa ahi es lo que en el documento real quedaria cortado.
    html = f"""<!doctype html><html lang="es-MX"><head><meta charset="utf-8"><style>
*{{box-sizing:border-box}}html,body{{margin:0;padding:0;background:#fff;
font-family:Arial,Helvetica,sans-serif}}
.caso{{width:149mm;height:136mm;margin:0 0 8mm;position:relative}}
{album.CSS}
{CSS_ORIGINAL}
</style></head><body>{''.join(partes)}</body></html>"""

    SALIDA.mkdir(parents=True, exist_ok=True)
    destino_html = SALIDA / "porque-una-regla.html"
    destino_html.write_text(html, encoding="utf-8")

    trozos = {}
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pag = navegador.new_page(viewport={"width": 800, "height": 900}, device_scale_factor=ESCALA)
        pag.goto(destino_html.as_uri())
        pag.wait_for_load_state("networkidle")
        for n in CASOS:
            for clave in ("viejo", "nuevo"):
                ruta = SALIDA / f"pq-{clave}{n}.png"
                pag.locator(f"#{clave}{n}").screenshot(path=str(ruta))
                trozos[f"{clave}{n}"] = ruta
        navegador.close()

    ANCHO, MARGEN, CABECERA, ETIQUETA = 640, 18, 54, 54
    muestra = Image.open(trozos["viejo13"])
    alto = round(ANCHO * muestra.height / muestra.width)
    hoja = Image.new(
        "RGB",
        (MARGEN * 3 + ANCHO * 2, CABECERA + MARGEN + 2 * (alto + ETIQUETA) + MARGEN + MARGEN),
        "#ffffff",
    )
    dib = ImageDraw.Draw(hoja)
    dib.rectangle([0, 0, hoja.width, CABECERA], fill="#0b2d56")
    dib.text(
        (MARGEN, 18),
        "DulceAuto · por qué la rejilla del diseño necesita una regla",
        font=fuente(20),
        fill="#ffffff",
    )

    textos = {
        "viejo13": ("Diseño original · 13 fotografías", "queda un hueco abajo a la derecha", "#b3261e"),
        "nuevo13": ("Regla única · 13 fotografías", album.repartir(13).resumen(), "#1a7f43"),
        "viejo15": ("Diseño original · 15 fotografías",
                    "la 15 queda debajo del álbum y sale de 2 px de alto: no se ve", "#b3261e"),
        "nuevo15": ("Regla única · 15 fotografías", album.repartir(15).resumen(), "#1a7f43"),
    }
    orden = ["viejo13", "nuevo13", "viejo15", "nuevo15"]
    for i, clave in enumerate(orden):
        col, fil = i % 2, i // 2
        x = MARGEN + col * (ANCHO + MARGEN)
        y = CABECERA + MARGEN + fil * (alto + ETIQUETA)
        img = Image.open(trozos[clave]).convert("RGB").resize((ANCHO, alto), Image.LANCZOS)
        hoja.paste(img, (x, y))
        titulo, pie, color = textos[clave]
        dib.text((x + 2, y + alto + 6), titulo, font=fuente(16), fill="#0b1f3a")
        dib.text((x + 2, y + alto + 28), pie, font=fuente(13), fill=color)

    destino = SALIDA / "porque-una-sola-regla.jpg"
    hoja.save(destino, quality=92)
    print(f"  {destino.name}  {hoja.width}x{hoja.height}px")
    if hoja.width > 2000 or hoja.height > 2000:
        raise SystemExit("Se pasó de 2000px.")


if __name__ == "__main__":
    main()
