"""
Tres pruebas reales de la pagina 2 dentro del backend: 5, 14 y 20 fotografias.

No monta el HTML a mano. Entra al panel como el administrador, sube un ZIP por
la misma ruta que usaria el vendedor, marca las verificaciones en su formulario
y fotografia la pagina 2 tal como la sirve la aplicacion. Si algo del camino
-la validacion del ZIP, el orden, el reparto, el motor de plantillas- estuviera
mal, saldria en la imagen.

    python demo_pagina2.py [http://127.0.0.1:8742] [directorio_de_salida]
"""
import io
import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from app import verificaciones  # noqa: E402
from hoja_contactos_album import FOTOS, fuente  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8742"
SALIDA = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).parent / "hoja-contactos"
USER, PASSWORD = "admin", "DulceAuto2026"

# Los tres casos que pidio el cliente, con las verificaciones que se marcan en
# cada uno. Se eligen distintas a proposito: 6, 4 y 2, para ver el panel a dos
# filas, a dos filas justas y a una sola.
CASOS = (
    (5, [v.clave for v in verificaciones.VERIFICACIONES]),
    (14, [v.clave for v in verificaciones.VERIFICACIONES[:4]]),
    (20, ["robo", "historial"]),
)


def zip_de(cuantas: int) -> bytes:
    """Un ZIP con `cuantas` fotografias, numeradas de forma que el orden
    alfabetico y el natural NO coincidan: foto2 y foto10 estan a proposito sin
    ceros delante, para que se vea si el orden se respeta."""
    fuentes = sorted(p for p in FOTOS.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    if not fuentes:
        raise SystemExit(f"No hay fotografías en {FOTOS}")
    memoria = io.BytesIO()
    with zipfile.ZipFile(memoria, "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(1, cuantas + 1):
            z.writestr(f"foto{i}.jpg", fuentes[(i - 1) % len(fuentes)].read_bytes())
    return memoria.getvalue()


def main() -> None:
    SALIDA.mkdir(parents=True, exist_ok=True)
    tomas = {}

    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pag = navegador.new_page(viewport={"width": 1280, "height": 900})

        pag.goto(f"{BASE}/acceso")
        pag.fill('input[name="username"]', USER)
        pag.fill('input[name="password"]', PASSWORD)
        pag.click('button[type="submit"]')

        pag.goto(f"{BASE}/facturas")
        enlace = pag.locator('tbody tr:not(.empty-row) a[href*="/editar"]').first
        destino = enlace.get_attribute("href")
        factura_id = destino.split("/facturas/")[1].split("/")[0]
        print(f"Factura de pruebas: {factura_id}")

        for cuantas, marcadas in CASOS:
            print(f"\n--- {cuantas} fotografías ---")
            # 1 · el ZIP, por la misma ruta que usaria el vendedor
            respuesta = pag.request.post(
                f"{BASE}/facturas/{factura_id}/album",
                multipart={
                    "album_zip": {
                        "name": f"album-{cuantas}.zip",
                        "mimeType": "application/zip",
                        "buffer": zip_de(cuantas),
                    }
                },
            )
            print(f"  subida ZIP: {respuesta.status}")

            # 2 · las verificaciones, por su formulario
            # Se manda el cuerpo a mano y no con form=: el formulario repite la
            # misma clave una vez por casilla, y un diccionario no puede
            # expresar una clave repetida.
            pag.request.post(
                f"{BASE}/facturas/{factura_id}/verificaciones",
                headers={"content-type": "application/x-www-form-urlencoded"},
                data="&".join(f"verificacion={c}" for c in marcadas),
            )
            print(f"  verificaciones marcadas: {len(marcadas)}")

            # 3 · la pagina 2, tal como la sirve la aplicacion.
            #
            # Se pide /documento y no la vista previa. La vista previa ensena el
            # mismo HTML, pero dentro de un iframe reducido con transform, y una
            # captura de un elemento con un transform por encima sale recortada:
            # la primera vez salio la cabecera cortada y texto de la pagina 1
            # encima, y no era un fallo del documento sino de la captura.
            # /documento es ademas la URL que imprime el generador de PDF.
            pag.goto(f"{BASE}/facturas/{factura_id}/documento")
            pag.wait_for_load_state("networkidle")
            objetivo = pag.locator(".pagina2")
            objetivo.scroll_into_view_if_needed()
            ruta = SALIDA / f"pagina2-{cuantas:02d}.png"
            objetivo.screenshot(path=str(ruta))
            tomas[cuantas] = ruta
            print(f"  captura: {ruta.name}")

        navegador.close()

    # --- lamina de las tres ---------------------------------------------
    ANCHO, MARGEN, CABECERA, ETIQUETA = 600, 16, 52, 46
    muestra = Image.open(tomas[5])
    alto = round(ANCHO * muestra.height / muestra.width)
    hoja = Image.new("RGB", (MARGEN * 4 + ANCHO * 3, CABECERA + MARGEN + alto + ETIQUETA + MARGEN), "#fff")
    dib = ImageDraw.Draw(hoja)
    dib.rectangle([0, 0, hoja.width, CABECERA], fill="#0b2d56")
    dib.text((MARGEN, 17), "DulceAuto · Página 2 real, generada por el backend",
             font=fuente(20), fill="#fff")

    for i, (cuantas, marcadas) in enumerate(CASOS):
        x = MARGEN + i * (ANCHO + MARGEN)
        y = CABECERA + MARGEN
        img = Image.open(tomas[cuantas]).convert("RGB").resize((ANCHO, alto), Image.LANCZOS)
        hoja.paste(img, (x, y))
        dib.rectangle([x, y, x + ANCHO - 1, y + alto - 1], outline="#c8d2de")
        dib.text((x + 2, y + alto + 5), f"{cuantas} fotografías", font=fuente(16), fill="#0b1f3a")
        dib.text((x + 2, y + alto + 26),
                 f"{len(marcadas)} verificación{'' if len(marcadas) == 1 else 'es'} marcada"
                 f"{'' if len(marcadas) == 1 else 's'}",
                 font=fuente(13), fill="#6b7a8d")

    destino = SALIDA / "pagina2-backend-5-14-20.jpg"
    hoja.save(destino, quality=92)
    print(f"\n  {destino.name}  {hoja.width}x{hoja.height}px")
    if hoja.width > 2000 or hoja.height > 2000:
        raise SystemExit("Se pasó de 2000px.")


if __name__ == "__main__":
    main()
