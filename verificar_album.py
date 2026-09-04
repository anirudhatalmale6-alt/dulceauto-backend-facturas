"""
Comprobacion de la regla de la rejilla del album.

La mitad de las comprobaciones son sobre el modulo y la otra mitad sobre lo que
Chromium pinta de verdad. Las segundas son las que importan: que el reparto
cuadre en el papel no demuestra que el navegador lo respete, y el PDF lo hace
Chromium, no este modulo.

    python verificar_album.py
"""
import base64
import mimetypes
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from app import album  # noqa: E402

# Las fotografias con las que se renderiza. Si hay un directorio con imagenes
# reales se usan; si no, se dibujan recuadros de colores. Lo que se mide aqui
# son tamanos y posiciones, y para eso da igual lo que haya dentro de la foto:
# asi la bateria corre igual en el servidor del cliente, donde no hay ninguna
# carpeta de fotos de ejemplo.
FOTOS = Path(os.environ.get("ALBUM_FOTOS") or (Path(__file__).parent / "fotos-album"))

# 136mm es el hueco que el diseno reserva al album dentro de la hoja A4. En
# pixeles de CSS, a 96 puntos por pulgada.
ALTO_ESPERADO_PX = 136.0 / 25.4 * 96.0
ANCHO_ESPERADO_PX = 149.0 / 25.4 * 96.0
TOLERANCIA_PX = 0.5

ok, fallos = [], []


def check(nombre, condicion, extra=""):
    (ok if condicion else fallos).append(nombre)
    print(("  OK   " if condicion else "  FALLA") + f" {nombre}" + (f"  [{extra}]" if extra else ""))


# --- 1 · el reparto ---------------------------------------------------------

print("\n1 · Cada album tiene todas sus fotografias y ninguna de mas")
for n in range(1, album.MAX_FOTOS + 1):
    reparto = album.repartir(n)
    indices = [f.indice for f in reparto.todas_las_fotos()]
    check(f"{n:2d} fotografías: salen las {n}, una sola vez", indices == list(range(1, n + 1)), str(indices))

print("\n2 · La regla es determinista")
for n in (1, 5, 13, 14, 20):
    a, b = album.repartir(n), album.repartir(n)
    check(f"{n:2d} fotografías dan siempre el mismo reparto", a.resumen() == b.resumen(), a.resumen())

print("\n3 · Ninguna fotografía sale con una forma imposible")
for n in range(1, album.MAX_FOTOS + 1):
    reparto = album.repartir(n)
    peor = min(reparto.todas_las_fotos(), key=lambda f: min(f.proporcion, 1 / f.proporcion))
    dentro = album.PROPORCION_MINIMA <= peor.proporcion <= album.PROPORCION_MAXIMA
    check(f"{n:2d} fotografías: la peor proporción es aceptable", dentro, f"{peor.proporcion:.2f}")

print("\n4 · El bloque destacado aparece y crece de forma coherente")
# Lo que se comprueba aqui es que no haya saltos: que no haya foto grande con
# 11 y rejilla plana con 12. Un salto asi es lo primero que se le nota a un
# documento que se genera solo.
for n in range(1, album.MAX_FOTOS + 1):
    reparto = album.repartir(n)
    esperado = n >= album.DESDE_DESTACADA
    check(f"{n:2d} fotografías: bloque destacado = {esperado}", reparto.tiene_bloque == esperado)

anchos = [album.repartir(n).ancho_bloque for n in range(album.DESDE_DESTACADA, album.MAX_FOTOS + 1)]
check("el ancho del bloque nunca vuelve a subir", all(a >= b for a, b in zip(anchos, anchos[1:])), str(sorted(set(anchos))))

laterales = {n: sum(len(f) for f in album.repartir(n).filas_laterales)
             for n in range(album.DESDE_DESTACADA, album.MAX_FOTOS + 1)}
check("las fotos del lateral nunca bajan al subir el total",
      all(laterales[n] <= laterales[n + 1] for n in range(album.DESDE_DESTACADA, album.MAX_FOTOS)),
      str(sorted(set(laterales.values()))))

print("\n5 · Ninguna fila se queda vacía")
for n in range(1, album.MAX_FOTOS + 1):
    reparto = album.repartir(n)
    filas = list(reparto.filas_laterales) + list(reparto.filas)
    check(f"{n:2d} fotografías: todas las filas llevan alguna", all(len(f) >= 1 for f in filas),
          str([len(f) for f in filas]))

print("\n6 · Los límites avisan en vez de dibujar cualquier cosa")
for malo in (0, -3, album.MAX_FOTOS + 1, 100):
    try:
        album.repartir(malo)
        check(f"{malo} fotografías da error", False, "no dio error")
    except ValueError:
        check(f"{malo} fotografías da error", True)


# --- 7 · lo que pinta Chromium ---------------------------------------------

def urls_de_fotos():
    urls = []
    if FOTOS.is_dir():
        for ruta in sorted(FOTOS.iterdir()):
            tipo = mimetypes.guess_type(ruta.name)[0] or ""
            if tipo.startswith("image/"):
                urls.append(f"data:{tipo};base64,{base64.b64encode(ruta.read_bytes()).decode()}")
    if urls:
        return urls

    colores = ("#2f6f9f", "#7a4f9c", "#3f8f5f", "#b07a2a", "#9c4545",
               "#3a7f8f", "#6f6f2f", "#8f4f7f", "#4f5f9f", "#5f8f3f",
               "#8f6f4f", "#4f8f8f", "#7f3f5f", "#5f5f5f")
    for i, color in enumerate(colores, start=1):
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300">'
            f'<rect width="400" height="300" fill="{color}"/>'
            f'<text x="200" y="180" font-size="120" fill="#ffffff" '
            f'text-anchor="middle" font-family="sans-serif">{i}</text></svg>'
        )
        urls.append("data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode())
    return urls


print("\n7 · Lo que Chromium pinta de verdad")
urls = urls_de_fotos()
print(f"  (usando {len(urls)} imágenes de {FOTOS if FOTOS.is_dir() else 'recuadros generados'})")
if True:
    bloques = []
    for n in range(1, album.MAX_FOTOS + 1):
        marcado = album.marcado(album.repartir(n), lambda i: urls[(i - 1) % len(urls)])
        bloques.append(f'<div class="caso" id="caso{n}">{marcado}</div>')
    html = (
        '<!doctype html><html lang="es-MX"><head><meta charset="utf-8"><style>'
        "*{box-sizing:border-box}html,body{margin:0;padding:0;background:#fff;"
        "font-family:Arial,Helvetica,sans-serif}.caso{width:149mm;margin:0 0 8mm}"
        + album.CSS
        + "</style></head><body>"
        + "".join(bloques)
        + "</body></html>"
    )
    destino = Path("/tmp/claude-1002/-home-freelancer/2b22cb8d-c5b4-4160-9778-a8e2f55d8d2e/scratchpad/hitoB/verificar-album.html")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(html, encoding="utf-8")

    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pag = navegador.new_page(viewport={"width": 900, "height": 900})
        pag.goto(destino.as_uri())
        pag.wait_for_load_state("networkidle")
        medidas = pag.evaluate(
            """() => {
              const salida = [];
              document.querySelectorAll('.caso').forEach(caso => {
                const alb = caso.querySelector('.album');
                const r = alb.getBoundingClientRect();
                const fotos = [...alb.querySelectorAll('.photo')].map(f => {
                  const fr = f.getBoundingClientRect();
                  return {w: fr.width, h: fr.height, top: fr.top - r.top,
                          left: fr.left - r.left, bottom: fr.bottom - r.top,
                          right: fr.right - r.left};
                });
                salida.push({
                  id: caso.id, w: r.width, h: r.height,
                  desborda: alb.scrollHeight - alb.clientHeight,
                  desbordaAncho: alb.scrollWidth - alb.clientWidth,
                  fotos,
                });
              });
              return salida;
            }"""
        )
        navegador.close()

    for m in medidas:
        n = int(m["id"].replace("caso", ""))

        check(
            f"{n:2d} fotografías: el álbum mide los 136mm reservados",
            abs(m["h"] - ALTO_ESPERADO_PX) <= TOLERANCIA_PX,
            f'{m["h"]:.1f}px vs {ALTO_ESPERADO_PX:.1f}px',
        )
        check(
            f"{n:2d} fotografías: y los 149mm de ancho",
            abs(m["w"] - ANCHO_ESPERADO_PX) <= TOLERANCIA_PX,
            f'{m["w"]:.1f}px',
        )
        check(
            f"{n:2d} fotografías: no se sale de su hueco",
            m["desborda"] <= 1 and m["desbordaAncho"] <= 1,
            f'alto +{m["desborda"]} ancho +{m["desbordaAncho"]}',
        )
        check(f"{n:2d} fotografías: hay {n} recuadros pintados", len(m["fotos"]) == n, str(len(m["fotos"])))

        # Que ninguna foto se salga por abajo o por la derecha del album. Esto
        # es lo que fallaba en la rejilla escrita a mano al pasar de 14.
        relleno = album.SEPARACION_MM / 25.4 * 96.0
        fuera = [
            f for f in m["fotos"]
            if f["bottom"] > m["h"] - relleno + 1 or f["right"] > m["w"] - relleno + 1
        ]
        check(f"{n:2d} fotografías: ninguna se sale del marco", not fuera, f"{len(fuera)} fuera")

        # Y que no quede un hueco: la fila de mas abajo tiene que llegar al
        # borde inferior. Es la comprobacion contraria a la anterior y es la
        # que caza el caso de 13 en la rejilla vieja.
        mas_abajo = max(f["bottom"] for f in m["fotos"])
        check(
            f"{n:2d} fotografías: la última fila llega al borde",
            abs(mas_abajo - (m["h"] - relleno)) <= 1.5,
            f'{mas_abajo:.1f} vs {m["h"] - relleno:.1f}',
        )

        anchos_reales = [f["w"] for f in m["fotos"]]
        altos_reales = [f["h"] for f in m["fotos"]]
        check(
            f"{n:2d} fotografías: ningún recuadro queda a cero",
            min(anchos_reales) > 8 and min(altos_reales) > 8,
            f"{min(anchos_reales):.1f}x{min(altos_reales):.1f}px",
        )

        # Lo que dice el modulo y lo que mide el navegador tienen que coincidir.
        # Si se separan, el reparto que se aprobo en la hoja de contactos no es
        # el que se acaba imprimiendo.
        reparto = album.repartir(n)
        por_indice = {f.indice: f for f in reparto.todas_las_fotos()}
        pixeles_por_mm = 96.0 / 25.4
        peor = 0.0
        for i, medida in enumerate(m["fotos"], start=1):
            previsto = por_indice[i]
            peor = max(
                peor,
                abs(medida["w"] - previsto.ancho_mm * pixeles_por_mm),
                abs(medida["h"] - previsto.alto_mm * pixeles_por_mm),
            )
        check(f"{n:2d} fotografías: los milímetros calculados son los pintados", peor <= 1.0, f"{peor:.2f}px")



# --- 8 · control positivo ---------------------------------------------------
#
# Todo lo de arriba sale en verde. Eso solo vale algo si estas mismas
# comprobaciones son capaces de ponerse en rojo, asi que se pasan tal cual por
# la rejilla ESCRITA A MANO del diseno original, que es la que hay que
# sustituir. Si tambien saliera verde con ella, las comprobaciones no estarian
# midiendo nada.

from comparativa_album_14 import CSS_ORIGINAL, original as marcado_original  # noqa: E402

print("\n8 · Control positivo: la rejilla vieja tiene que FALLAR aquí")
if True:
    def album_viejo(cuantas: int) -> str:
        """La rejilla original recortada o estirada a `cuantas` fotografias,
        que es exactamente lo que pasaria si se dejara como estaba y el
        vendedor subiera 13 o 15 en vez de 14."""
        figuras = []
        for i in range(1, cuantas + 1):
            clase = "main" if i == 1 else f"p{i}"
            figuras.append(
                f'<figure class="photo {clase}"><img src="{urls[(i - 1) % len(urls)]}" '
                f'alt="Foto {i}"><figcaption>{i:02d}</figcaption></figure>'
            )
        return '<div class="album-orig">' + "".join(figuras) + "</div>"

    casos = []
    for cuantas in (13, 14, 15):
        casos.append(f'<div class="caso" id="viejo{cuantas}">{album_viejo(cuantas)}</div>')
        casos.append(
            f'<div class="caso" id="nuevo{cuantas}">'
            + album.marcado(album.repartir(cuantas), lambda i: urls[(i - 1) % len(urls)])
            + "</div>"
        )

    html_control = (
        '<!doctype html><html lang="es-MX"><head><meta charset="utf-8"><style>'
        "*{box-sizing:border-box}html,body{margin:0;padding:0;background:#fff;"
        "font-family:Arial,Helvetica,sans-serif}.caso{width:149mm;margin:0 0 8mm}"
        + album.CSS + CSS_ORIGINAL
        + "</style></head><body>" + "".join(casos) + "</body></html>"
    )
    destino_control = destino.with_name("control-positivo-album.html")
    destino_control.write_text(html_control, encoding="utf-8")

    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pag = navegador.new_page(viewport={"width": 900, "height": 900})
        pag.goto(destino_control.as_uri())
        pag.wait_for_load_state("networkidle")
        control_medidas = pag.evaluate(
            """() => {
              const salida = {};
              document.querySelectorAll('.caso').forEach(caso => {
                const alb = caso.querySelector('.album, .album-orig');
                const r = alb.getBoundingClientRect();
                let cubierto = 0;
                alb.querySelectorAll('.photo').forEach(f => {
                  const fr = f.getBoundingClientRect();
                  cubierto += fr.width * fr.height;
                });
                const fotos = [...alb.querySelectorAll('.photo')];
                const ultima = fotos[fotos.length - 1].getBoundingClientRect();
                salida[caso.id] = {
                  h: r.height,
                  desborda: alb.scrollHeight - alb.clientHeight,
                  cubierto,
                  ultima_alto: ultima.height,
                  ultima_top: ultima.top - r.top,
                };
              });
              return salida;
            }"""
        )
        navegador.close()

    # 15 fotografias: la rejilla vieja solo tiene cinco filas declaradas, asi
    # que la decimoquinta se crea una fila de mas y el album se sale de la hoja.
    viejo15 = control_medidas["viejo15"]
    check(
        "la rejilla vieja SE SALE con 15 fotografías",
        viejo15["desborda"] > 1,
        f'sobresale {viejo15["desborda"]}px',
    )
    check(
        "la fotografía 15 sobrante queda invisible en la rejilla vieja",
        viejo15["ultima_alto"] < 5 and viejo15["ultima_top"] > viejo15["h"] - 5,
        f'{viejo15["ultima_alto"]:.0f}px de alto, a {viejo15["ultima_top"]:.0f}px del borde superior '
        f'de un álbum de {viejo15["h"]:.0f}px',
    )
    check(
        "y la nueva no",
        control_medidas["nuevo15"]["desborda"] <= 1,
        f'sobresale {control_medidas["nuevo15"]["desborda"]}px',
    )

    # 13 fotografias: caben, pero la ultima celda se queda vacia. Se mide
    # comparando cuanta superficie llega a cubrirse con la del caso de 14, que
    # es el unico para el que esa rejilla estaba pensada.
    hueco = 1 - control_medidas["viejo13"]["cubierto"] / control_medidas["viejo14"]["cubierto"]
    check(
        "la rejilla vieja DEJA UN HUECO con 13 fotografías",
        hueco > 0.02,
        f"cubre un {hueco * 100:.0f}% menos que con 14",
    )
    for cuantas in (13, 15):
        misma = abs(
            1 - control_medidas[f"nuevo{cuantas}"]["cubierto"] / control_medidas["nuevo14"]["cubierto"]
        )
        check(
            f"la nueva cubre lo mismo con {cuantas} que con 14",
            misma < 0.02,
            f"diferencia {misma * 100:.1f}%",
        )

print("\n" + "=" * 58)
print(f"{len(ok)} comprobaciones correctas, {len(fallos)} fallos")
if fallos:
    for f in fallos:
        print(f"  FALLA: {f}")
    sys.exit(1)
print("Regla del álbum verificada.")
