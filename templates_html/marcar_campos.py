"""
Marcado de los huecos dinamicos en las tres plantillas aprobadas.

Este archivo se ejecuto una sola vez y se guarda para que quede por escrito
exactamente que se le hizo a las plantillas aprobadas: **anadir atributos**.
Ni una etiqueta, ni una clase, ni un texto, ni un espacio se han tocado.

    python marcar_campos.py            comprueba que ya estan marcadas
    python marcar_campos.py --aplicar  aplica el marcado

Los atributos que se anaden son tres:

  data-field="..."         el hueco lleva un dato de la factura
  data-step="1..4"         paso de la barra de progreso, para poder marcar
                           cual esta hecho y cual activo segun el estado
  data-hide-if-empty="..." el elemento entero desaparece si ese dato esta
                           vacio (la pastilla de descuento; una pastilla vacia
                           con el icono dentro quedaria fea y sin sentido)

La comprobacion de que esto es cierto no se basa en mi palabra: en
verificar_fase_c.py se quitan esos tres atributos del archivo marcado y el
resultado tiene que ser identico byte a byte al de aprobado-original/.

Los anclajes son estructurales (clases y etiquetas) y no dependen del idioma,
que es la unica manera de aplicar lo mismo a las tres versiones sin escribir
tres veces las mismas reglas.
"""
import re
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
ARCHIVOS = ["es-MX/factura.html", "en/invoice.html", "es-AR/factura.html"]


def marcar(html: str) -> str:
    # 1 · Pastilla de descuento: desaparece entera si no hay descuento.
    html = html.replace(
        '<span class="discount-pill">',
        '<span class="discount-pill" data-hide-if-empty="descuento">',
    )

    # 2 · Fila de seguro incluido de la tabla de la operacion.
    html = re.sub(
        r'<td class="included">([^<]*)</td>',
        r'<td class="included" data-field="cobertura">\1</td>',
        html,
    )

    # 3 · Fila de transporte. El ancla es la celda de cantidad "1" seguida de
    #     una celda sin atributos: la fila del vehiculo, que tambien lleva un
    #     "1", tiene data-field en la celda del importe y no coincide.
    html = re.sub(
        r"(<td>1</td><td)(>[^<]*</td>)",
        r'\1 data-field="transporte"\2',
        html,
    )

    # 4 · Bloque de entrega. El primer bloque describe la modalidad elegida y
    #     el segundo la alternativa. Es el unico <a href="#"> del documento.
    html = re.sub(
        r'<p><a href="#">([^<]*)</a></p>\s*\n(\s*)<p>([^<]*)</p>',
        lambda m: (
            f'<p><a href="#" data-field="entrega_modalidad">{m.group(1)}</a></p>\n'
            f'{m.group(2)}<p data-field="entrega_texto">{m.group(3)}</p>'
        ),
        html,
    )
    html = re.sub(
        r"<p><strong>([^<]*)</strong></p>\s*\n(\s*)<p>([^<]*)</p>",
        lambda m: (
            f'<p><strong data-field="entrega_alternativa">{m.group(1)}</strong></p>\n'
            f'{m.group(2)}<p data-field="entrega_alternativa_texto">{m.group(3)}</p>'
        ),
        html,
    )

    # 4bis · Titular y linea de debajo. Dependen del estado: en "Pago pendiente"
    #        son los textos aprobados, pero en una factura ya entregada decir
    #        "Confirma el pago" contradice al resto del documento.
    html = re.sub(
        r"(<div class=\"intro-top\">\s*\n\s*)<h2>([^<]*)</h2>",
        lambda m: f'{m.group(1)}<h2 data-field="titular">{m.group(2)}</h2>',
        html,
    )
    html = re.sub(
        r"(<div class=\"intro-sub\">\s*\n\s*)<p>([^<]*)</p>",
        lambda m: f'{m.group(1)}<p data-field="titular_texto">{m.group(2)}</p>',
        html,
    )

    # 5 · Cargo del representante: el <small> que va justo detras de su nombre.
    html = html.replace("</h4><small>", '</h4><small data-field="agente_cargo">')

    # 6 · Codigo de barras y QR. Aqui solo se cambian atributos (el alt lleva
    #     el folio). La regeneracion de las dos imagenes es de la Fase D, y
    #     tenerlos ya marcados es justo lo que va a necesitar.
    html = html.replace('<img class="barcode" ', '<img class="barcode" data-field="codigo_barras" ')
    html = html.replace('<img class="qr-code" ', '<img class="qr-code" data-field="codigo_qr" ')

    # 7 · Pasos de la barra de progreso, numerados en el orden en que estan.
    contador = {"n": 0}

    def numerar(m):
        contador["n"] += 1
        return f'{m.group(1)} data-step="{contador["n"]}"{m.group(2)}'

    html = re.sub(r'(<div class="step(?: done| active)?")(>)', numerar, html)
    return html


def main() -> int:
    aplicar = "--aplicar" in sys.argv
    pendientes = 0
    for rel in ARCHIVOS:
        ruta = AQUI / rel
        actual = ruta.read_text(encoding="utf-8")
        marcado = marcar((AQUI / "aprobado-original" / rel).read_text(encoding="utf-8"))
        if actual == marcado:
            print(f"  ya marcado   {rel}")
            continue
        pendientes += 1
        if aplicar:
            ruta.write_text(marcado, encoding="utf-8")
            print(f"  marcado      {rel}")
        else:
            print(f"  SIN MARCAR   {rel}")
    return 1 if pendientes and not aplicar else 0


if __name__ == "__main__":
    sys.exit(main())
