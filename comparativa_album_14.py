"""
Compara, con las mismas fotografias, la rejilla escrita a mano del diseno
original -que solo sirve para 14- con la que sale de la regla unica.

Es la comprobacion de que pasar a una sola regla no estropea el caso para el
que estaba pensado el diseno.

    python comparativa_album_14.py [directorio_de_salida]
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from app import album  # noqa: E402
from hoja_contactos_album import ESCALA, SALIDA, fotos_en_linea, fuente  # noqa: E402

# La rejilla tal cual venia en DulceAuto_Pagina2_Album_Verificacion_Premium
# _POLISH.html: cinco filas de alturas fijas y catorce reglas de colocacion.
CSS_ORIGINAL = """
.album-orig{height:136mm;display:grid;
  grid-template-columns:repeat(4,minmax(0,1fr));
  grid-template-rows:29mm 29mm 23mm 23mm 24mm;gap:1.8mm;padding:1.8mm;
  border:.3mm solid #d8dee7;border-top:.2mm solid #edf0f4;
  border-radius:0 0 2.7mm 2.7mm;background:#fbfcfe}
.album-orig .photo.main{grid-column:1/3;grid-row:1/3}
.album-orig .photo.p2{grid-column:3/5;grid-row:1}
.album-orig .photo.p3{grid-column:3;grid-row:2}
.album-orig .photo.p4{grid-column:4;grid-row:2}
.album-orig .photo.p5{grid-column:1;grid-row:3}
.album-orig .photo.p6{grid-column:2;grid-row:3}
.album-orig .photo.p7{grid-column:3;grid-row:3}
.album-orig .photo.p8{grid-column:4;grid-row:3}
.album-orig .photo.p9{grid-column:1;grid-row:4}
.album-orig .photo.p10{grid-column:2;grid-row:4}
.album-orig .photo.p11{grid-column:3;grid-row:4}
.album-orig .photo.p12{grid-column:4;grid-row:4}
.album-orig .photo.p13{grid-column:1/3;grid-row:5}
.album-orig .photo.p14{grid-column:3/5;grid-row:5}
"""


def original(urls: list[str]) -> str:
    figuras = []
    for i in range(1, 15):
        clase = "main" if i == 1 else f"p{i}"
        figuras.append(
            f'<figure class="photo {clase}"><img src="{urls[(i - 1) % len(urls)]}" '
            f'alt="Foto {i}"><figcaption>{i:02d}</figcaption></figure>'
        )
    return '<div class="album-orig">' + "".join(figuras) + "</div>"


def main() -> None:
    urls = fotos_en_linea()
    reparto = album.repartir(14)
    html = f"""<!doctype html><html lang="es-MX"><head><meta charset="utf-8"><style>
*{{box-sizing:border-box}}html,body{{margin:0;padding:0;background:#fff;
font-family:Arial,Helvetica,sans-serif}}
.caso{{width:149mm;margin:0 0 8mm}}
{album.CSS}
{CSS_ORIGINAL}
</style></head><body>
<div class="caso" id="orig">{original(urls)}</div>
<div class="caso" id="nuevo">{album.marcado(reparto, lambda i: urls[(i - 1) % len(urls)])}</div>
</body></html>"""

    SALIDA.mkdir(parents=True, exist_ok=True)
    destino_html = SALIDA / "comparativa14.html"
    destino_html.write_text(html, encoding="utf-8")

    trozos = {}
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pag = navegador.new_page(viewport={"width": 800, "height": 900}, device_scale_factor=ESCALA)
        pag.goto(destino_html.as_uri())
        pag.wait_for_load_state("networkidle")
        for clave, selector in (("orig", "#orig .album-orig"), ("nuevo", "#nuevo .album")):
            ruta = SALIDA / f"cmp-{clave}.png"
            pag.locator(selector).screenshot(path=str(ruta))
            trozos[clave] = ruta
        navegador.close()

    ANCHO, MARGEN, CABECERA, ETIQUETA = 700, 18, 54, 52
    muestra = Image.open(trozos["orig"])
    alto = round(ANCHO * muestra.height / muestra.width)
    hoja = Image.new("RGB", (MARGEN * 3 + ANCHO * 2, CABECERA + MARGEN + alto + ETIQUETA + MARGEN), "#fff")
    dib = ImageDraw.Draw(hoja)
    dib.rectangle([0, 0, hoja.width, CABECERA], fill="#0b2d56")
    dib.text((MARGEN, 18), "DulceAuto · el mismo caso de 14 fotografías, con las dos rejillas",
             font=fuente(20), fill="#fff")

    textos = [
        ("orig", "Diseño original", "rejilla escrita a mano · sirve solo para 14"),
        ("nuevo", "Regla única", f"{reparto.resumen()} · sirve de 1 a 20"),
    ]
    for i, (clave, titulo, pie) in enumerate(textos):
        x = MARGEN + i * (ANCHO + MARGEN)
        y = CABECERA + MARGEN
        img = Image.open(trozos[clave]).convert("RGB").resize((ANCHO, alto), Image.LANCZOS)
        hoja.paste(img, (x, y))
        dib.rectangle([x, y, x + ANCHO - 1, y + alto - 1], outline="#c8d2de")
        dib.text((x + 2, y + alto + 8), titulo, font=fuente(17), fill="#0b1f3a")
        dib.text((x + 2, y + alto + 30), pie, font=fuente(13), fill="#6b7a8d")

    destino = SALIDA / "comparativa-14-fotografias.jpg"
    hoja.save(destino, quality=92)
    print(f"  {destino.name}  {hoja.width}x{hoja.height}px")
    if hoja.width > 2000 or hoja.height > 2000:
        raise SystemExit("Se paso de 2000px.")


if __name__ == "__main__":
    main()
