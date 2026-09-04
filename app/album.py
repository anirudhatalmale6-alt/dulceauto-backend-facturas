"""
La regla de la rejilla del album fotografico.

El diseno original traia la rejilla escrita a mano para 14 fotografias: cinco
filas de alturas distintas (29mm, 29mm, 23mm, 23mm, 24mm) y catorce reglas
.p2 .. .p14 colocando cada foto en su celda. Eso funciona para 14 y solo para
14: con 13 queda un hueco y con 15 la foto sobrante se sale de la hoja.

Aqui hay UNA sola regla que sirve de 1 a 20.

La regla, en tres frases:

  1. El album se parte en filas de la MISMA altura. Como las alturas se
     reparten en proporcion y no en milimetros fijos, el album ocupa siempre
     exactamente los 136mm que tiene reservados en la hoja, tenga 1 fotografia
     o tenga 20. Ni un milimetro de hueco ni uno de desbordamiento.

  2. Las fotografias de una fila se reparten el ancho a partes iguales. Por eso
     nunca queda un agujero: no hay "celdas sobrantes" que rellenar, cada fila
     se ajusta al numero de fotos que le toca.

  3. A partir de cinco fotografias, la primera ocupa un bloque destacado en la
     esquina superior izquierda -las dos primeras filas de alto- igual que en
     el diseno original. Con menos de cinco no hay bloque destacado: destacar
     una de tres deja a las otras dos convertidas en tiras.

Lo unico que se calcula es cuantas filas hay y cuantas fotos van en cada una.
Se eligen probando todos los repartos posibles y quedandose con el que deja las
fotografias mas cerca de una proporcion apaisada. Apaisada y no cuadrada porque
un coche recortado en vertical se ve por el centro de la puerta y no se
entiende; recortado a lo ancho se ve entero.
"""

import math
from html import escape

# Medidas reales del hueco del album dentro de la hoja A4, en milimetros. Salen
# de la hoja: 210mm de ancho menos 6.8mm de margen a cada lado son 196.4mm de
# contenido; de ahi, 44mm se los lleva la columna lateral y 3.4mm la separacion,
# lo que deja 149mm de ancho y los 136mm de alto que el diseno reservaba. A eso
# se le quitan los 1.8mm de relleno que el marco del album tiene por cada lado.
#
# El marco tambien ocupa. Son decimas de milimetro, pero son las que separaban
# lo que este modulo calculaba de lo que Chromium pintaba: con una sola
# fotografia, las decimas no se reparten entre veinte celdas y la diferencia
# salia entera, 2 pixeles. Se descuentan aqui para que los milimetros de este
# modulo sean los de la hoja y no una aproximacion.
SEPARACION_MM = 1.8
BORDE_MM = 0.3
BORDE_SUPERIOR_MM = 0.2
ANCHO_MM = 149.0 - 2 * SEPARACION_MM - 2 * BORDE_MM
ALTO_MM = 136.0 - 2 * SEPARACION_MM - BORDE_SUPERIOR_MM - BORDE_MM

MAX_FOTOS = 20

# Proporcion (ancho/alto) a la que se tiende. 1.25 es un apaisado suave, entre
# el 4:3 de una camara de telefono y el 3:2 de una reflex.
PROPORCION_OBJETIVO = 1.25

# Limites duros. Por debajo, la foto es mas alta que ancha y el coche sale
# cortado por el centro. Por encima, es una tira.
PROPORCION_MINIMA = 0.88
PROPORCION_MAXIMA = 2.75

# A partir de aqui la primera fotografia ocupa el bloque destacado.
DESDE_DESTACADA = 5

# El bloque destacado ocupa siempre las dos primeras filas de alto. De ancho se
# queda con dos tercios del album cuando hay pocas fotografias y con la mitad
# -como en el diseno original- cuando hay muchas. El corte esta en 10 porque es
# donde deja de compensar: por encima, dos tercios aplasta demasiado a las que
# van al lado.
#
# Es un escalon fijo y no una eleccion del buscador a proposito. Si se dejara
# elegir, el ancho podria salir de dos tercios con 15 fotografias y de la mitad
# con 14 y con 16, y el cliente veria el album cambiar de forma sin motivo al
# subir una foto mas.
FILAS_DESTACADA = 2
HASTA_DESTACADA_ANCHA = 10


def _ancho_destacada(n: int) -> float:
    return 2 / 3 if n <= HASTA_DESTACADA_ANCHA else 1 / 2


def _fotos_laterales(n: int) -> int:
    """Cuantas fotografias van a la derecha del bloque destacado.

    Va atado al ancho del bloque: si el bloque se queda con dos tercios, al
    lado solo cabe una foto por fila; si se queda con la mitad, caben dos. Es
    otro escalon fijo, por el mismo motivo que el ancho: si se dejara elegir,
    con 15 fotografias saldria una columna lateral de dos fotos muy alargadas y
    con 14 y con 16 saldrian cuatro normales. La diferencia de calidad entre un
    reparto y otro era de milesimas; la de coherencia, evidente.
    """
    return FILAS_DESTACADA * (1 if _ancho_destacada(n) > 0.5 else 2)

MAX_FILAS = 7


class Foto:
    """Una fotografia ya colocada, con su tamano real en milimetros.

    `indice` es 1 para la primera del album. Los milimetros no se usan para
    pintar -eso lo hacen las proporciones del CSS- pero si para comprobar en
    las pruebas que ninguna foto sale con una forma imposible.
    """

    __slots__ = ("indice", "ancho_mm", "alto_mm", "destacada")

    def __init__(self, indice: int, ancho_mm: float, alto_mm: float, destacada: bool = False):
        self.indice = indice
        self.ancho_mm = ancho_mm
        self.alto_mm = alto_mm
        self.destacada = destacada

    @property
    def proporcion(self) -> float:
        return self.ancho_mm / self.alto_mm

    def __repr__(self) -> str:
        return f"<foto {self.indice} {self.ancho_mm:.1f}x{self.alto_mm:.1f}mm>"


class Fila:
    """Una fila de fotografias que se reparten el ancho a partes iguales."""

    __slots__ = ("fotos",)

    def __init__(self, fotos: list[Foto]):
        self.fotos = fotos

    def __len__(self) -> int:
        return len(self.fotos)

    def __repr__(self) -> str:
        return f"<fila {len(self.fotos)}>"


class Reparto:
    """Como quedan repartidas n fotografias.

    Dos formas posibles:

      - Sin bloque destacado: `bloque` es None y `filas` son todas las filas del
        album, una detras de otra.

      - Con bloque destacado: `bloque` es la fotografia 1, `filas_laterales` son
        las dos filas que van a su derecha, y `filas` son las que van debajo,
        a todo el ancho.
    """

    __slots__ = ("n", "total_filas", "bloque", "ancho_bloque", "filas_laterales", "filas")

    def __init__(self, n, total_filas, bloque, ancho_bloque, filas_laterales, filas):
        self.n = n
        self.total_filas = total_filas
        self.bloque = bloque
        self.ancho_bloque = ancho_bloque
        self.filas_laterales = filas_laterales
        self.filas = filas

    @property
    def tiene_bloque(self) -> bool:
        return self.bloque is not None

    def todas_las_fotos(self) -> list[Foto]:
        fotos = []
        if self.bloque is not None:
            fotos.append(self.bloque)
        for fila in self.filas_laterales:
            fotos.extend(fila.fotos)
        for fila in self.filas:
            fotos.extend(fila.fotos)
        return sorted(fotos, key=lambda f: f.indice)

    def resumen(self) -> str:
        """Una linea legible, para las pruebas y para la hoja de contactos."""
        partes = []
        if self.tiene_bloque:
            lados = "+".join(str(len(f)) for f in self.filas_laterales)
            partes.append(f"destacada({int(self.ancho_bloque * 100)}%)|{lados}")
        partes.extend(str(len(f)) for f in self.filas)
        return f"{self.total_filas} filas · " + " · ".join(partes)

    def __repr__(self) -> str:
        return f"<Reparto {self.n} fotos: {self.resumen()}>"


def _repartir_en(cuantas: int, grupos: int, mayores_al_final: bool = True) -> list[int] | None:
    """Reparte `cuantas` cosas en `grupos` lo mas parejo posible, sin dejar
    ningun grupo vacio. Devuelve None si no llegan."""
    if grupos <= 0:
        return None if cuantas else []
    if cuantas < grupos:
        return None
    base, resto = divmod(cuantas, grupos)
    tamanos = [base] * grupos
    # El resto se suma a las ULTIMAS filas: asi las de arriba llevan menos
    # fotos, que es lo mismo que decir que las llevan mas grandes, y el album
    # se lee de mayor a menor como en el diseno original.
    orden = range(grupos - 1, grupos - 1 - resto, -1) if mayores_al_final else range(resto)
    for i in orden:
        tamanos[i] += 1
    return tamanos


def _desviacion(proporcion: float) -> float:
    """Cuanto se aleja una foto de la proporcion buscada. Se mide en logaritmo
    para que quedarse a la mitad de ancho pese lo mismo que pasarse al doble;
    con una resta, lo ancho contaria siempre mas que lo estrecho."""
    return abs(math.log(proporcion / PROPORCION_OBJETIVO))


def _alto_fila(total_filas: int) -> float:
    """Alto de una fila, ya descontadas las separaciones entre filas."""
    return (ALTO_MM - (total_filas - 1) * SEPARACION_MM) / total_filas


def _ancho_celda(disponible: float, cuantas: int) -> float:
    """Ancho de cada foto de una fila, descontadas las separaciones."""
    return (disponible - (cuantas - 1) * SEPARACION_MM) / cuantas


def _construir(n: int, total_filas: int, ancho_bloque: float | None, en_lateral: int) -> Reparto | None:
    """Arma un reparto concreto. Devuelve None si no es viable."""
    alto_fila = _alto_fila(total_filas)

    if ancho_bloque is None:
        tamanos = _repartir_en(n, total_filas)
        if tamanos is None:
            return None
        filas, indice = [], 1
        for k in tamanos:
            ancho = _ancho_celda(ANCHO_MM, k)
            fotos = [Foto(indice + i, ancho, alto_fila) for i in range(k)]
            indice += k
            filas.append(Fila(fotos))
        return Reparto(n, total_filas, None, None, [], filas)

    if total_filas < FILAS_DESTACADA + 1:
        return None

    # El bloque destacado y la columna lateral se reparten el ancho con una
    # separacion en medio, igual que dos fotos de la misma fila.
    util = ANCHO_MM - SEPARACION_MM
    ancho_destacada = util * ancho_bloque
    ancho_lateral = util - ancho_destacada
    # De alto ocupa dos filas MAS la separacion que habria entre ellas, que es
    # exactamente lo que hace un grid-row: span 2.
    alto_destacada = alto_fila * FILAS_DESTACADA + SEPARACION_MM
    bloque = Foto(1, ancho_destacada, alto_destacada, destacada=True)

    laterales = _repartir_en(en_lateral, FILAS_DESTACADA)
    if laterales is None:
        return None

    debajo = n - 1 - en_lateral
    tamanos = _repartir_en(debajo, total_filas - FILAS_DESTACADA)
    if tamanos is None:
        return None

    indice = 2
    filas_laterales = []
    for k in laterales:
        ancho = _ancho_celda(ancho_lateral, k)
        fotos = [Foto(indice + i, ancho, alto_fila) for i in range(k)]
        indice += k
        filas_laterales.append(Fila(fotos))

    filas = []
    for k in tamanos:
        ancho = _ancho_celda(ANCHO_MM, k)
        fotos = [Foto(indice + i, ancho, alto_fila) for i in range(k)]
        indice += k
        filas.append(Fila(fotos))

    return Reparto(n, total_filas, bloque, ancho_bloque, filas_laterales, filas)


def _puntuar(reparto: Reparto) -> float | None:
    """Lo malo que es un reparto. Menos es mejor. None si es inaceptable."""
    fotos = reparto.todas_las_fotos()
    total = 0.0
    for foto in fotos:
        prop = foto.proporcion
        if not (PROPORCION_MINIMA <= prop <= PROPORCION_MAXIMA):
            return None
        # El bloque destacado pesa mas: es lo primero que se mira de la hoja.
        total += _desviacion(prop) * (2.0 if foto.destacada else 1.0)
    media = total / (len(fotos) + 1)

    # Entre dos repartos parecidos se prefiere el mas regular: filas con el
    # mismo numero de fotos se leen mejor que filas descabaladas.
    conteos = [len(f) for f in reparto.filas_laterales] + [len(f) for f in reparto.filas]
    irregularidad = (max(conteos) - min(conteos)) if conteos else 0
    return media + 0.02 * irregularidad


def repartir(n: int) -> Reparto:
    """La regla, en una sola llamada: cuantas filas y que fotografia va en cada
    sitio, para cualquier n entre 1 y MAX_FOTOS."""
    if n < 1:
        raise ValueError("Un album necesita al menos una fotografia.")
    if n > MAX_FOTOS:
        raise ValueError(f"El album admite hasta {MAX_FOTOS} fotografias, se pidieron {n}.")

    candidatos = _buscar(n, laterales_fijas=True)
    if not candidatos:
        # Con el numero de fotos laterales fijado no sale ningun reparto
        # aceptable. Antes de rendirse se prueba con cualquier reparto lateral.
        candidatos = _buscar(n, laterales_fijas=False)

    if not candidatos:
        # Red de seguridad. No deberia llegar aqui con n <= MAX_FOTOS, pero si
        # alguien mueve los limites, mejor un album feo que una excepcion en
        # mitad de la generacion de un PDF.
        filas_seguras = max(1, round(math.sqrt(n / 1.1)))
        reparto = _construir(n, min(filas_seguras, MAX_FILAS), None, 0)
        if reparto is None:
            reparto = _construir(n, 1, None, 0)
        return reparto

    candidatos.sort(key=lambda c: (round(c[0], 6), c[1]))
    return candidatos[0][2]


def _buscar(n: int, *, laterales_fijas: bool) -> list[tuple[float, int, Reparto]]:
    candidatos: list[tuple[float, int, Reparto]] = []
    for total_filas in range(1, MAX_FILAS + 1):
        # A partir de DESDE_DESTACADA el bloque destacado no es una opcion que
        # compita con las demas: es parte del diseno. Si se dejara competir,
        # ganaria la rejilla uniforme casi siempre y el album tendria foto
        # grande con 11 fotografias y no con 12, que es justo la incoherencia
        # que hay que evitar.
        opciones: list[tuple[float | None, int]] = []
        if n < DESDE_DESTACADA:
            opciones.append((None, 0))
        else:
            ancho = _ancho_destacada(n)
            if laterales_fijas:
                opciones.append((ancho, min(_fotos_laterales(n), n - 1)))
            else:
                # En el lateral tiene que haber al menos una foto por fila.
                for en_lateral in range(FILAS_DESTACADA, n):
                    opciones.append((ancho, en_lateral))
        for ancho, en_lateral in opciones:
            reparto = _construir(n, total_filas, ancho, en_lateral)
            if reparto is None:
                continue
            puntos = _puntuar(reparto)
            if puntos is None:
                continue
            # El desempate va por numero de filas para que la regla sea
            # determinista: el mismo n da siempre exactamente el mismo album.
            candidatos.append((puntos, total_filas, reparto))

    return candidatos


# --- el marcado -------------------------------------------------------------
#
# El HTML lo escribe esta misma funcion, no la plantilla, para que la hoja de
# contactos que aprueba el cliente y la pagina 2 del documento real salgan del
# mismo sitio. Si se escribieran por separado, la aprobacion valdria para una
# imagen y no para lo que se acaba imprimiendo.

_CSS_LEGIBLE = """
.album{display:grid;gap:%(sep)smm;padding:%(sep)smm;height:136mm;
  border:.3mm solid #d8dee7;border-top:.2mm solid #edf0f4;
  border-radius:0 0 2.7mm 2.7mm;background:#fbfcfe}
.album .banda,.album .lateral,.album .fila{display:grid;gap:%(sep)smm;min-width:0;min-height:0}
.album .banda{grid-row:span 2}
.photo{margin:0;border-radius:1.8mm;overflow:hidden;position:relative;
  background:#eef2f6;border:.22mm solid #d3dbe5;min-width:0;min-height:0}
.photo img{width:100%%;height:100%%;object-fit:cover;display:block}
.photo figcaption{position:absolute;right:1.2mm;bottom:1mm;
  background:rgba(11,31,58,.76);color:#fff;font-size:2mm;border-radius:5mm;
  padding:.35mm 1mm;letter-spacing:.05mm}
""" % {"sep": SEPARACION_MM}

# El CSS sale en UNA linea. Escrito arriba en varias porque asi se lee, y
# entregado en una porque entra dentro de un hueco de la plantilla: si metiera
# doce lineas donde el diseno tiene una, el documento generado dejaria de tener
# las mismas lineas que el archivo aprobado, y la comprobacion que vigila que el
# motor no toca el diseno se quedaria sin poder compararlos linea a linea.
CSS = " ".join(_CSS_LEGIBLE.split())


def _figura(foto: Foto, src: str, alt: str) -> str:
    """Una figura del album.

    El src y el alt se escapan SIEMPRE. El alt lleva el titulo del vehiculo,
    que lo escribe una persona en el panel: un titulo con una comilla doble
    cerraria el atributo y lo que viniera detras entraria en el documento como
    marcado. Es la clase de agujero que no se nota hasta que alguien lo busca.
    """
    clase = "photo destacada" if foto.destacada else "photo"
    return (
        f'<figure class="{clase}" data-photo-index="{foto.indice}">'
        f'<img src="{escape(src, quote=True)}" alt="{escape(alt, quote=True)}">'
        f"<figcaption>{foto.indice:02d}</figcaption></figure>"
    )


def _fila_html(fila: Fila, src, alt) -> str:
    columnas = f"repeat({len(fila)},minmax(0,1fr))"
    figuras = "".join(_figura(f, src(f.indice), alt(f.indice)) for f in fila.fotos)
    return f'<div class="fila" style="grid-template-columns:{columnas}">{figuras}</div>'


def marcado(reparto: Reparto, src, alt=None) -> str:
    """El contenido del album. `src` y `alt` reciben el numero de fotografia
    (1 para la primera) y devuelven la direccion de la imagen y su texto
    alternativo."""
    if alt is None:
        def alt(i):
            return f"Foto {i} del vehículo"

    filas = f"repeat({reparto.total_filas},minmax(0,1fr))"
    partes = []

    if reparto.tiene_bloque:
        # 2fr 1fr o 1fr 1fr, segun el ancho que le toque al bloque destacado.
        peso = 2 if reparto.ancho_bloque > 0.5 else 1
        laterales = "".join(_fila_html(f, src, alt) for f in reparto.filas_laterales)
        partes.append(
            f'<div class="banda" style="grid-template-columns:{peso}fr 1fr">'
            + _figura(reparto.bloque, src(1), alt(1))
            + f'<div class="lateral" style="grid-template-rows:repeat({FILAS_DESTACADA},minmax(0,1fr))">'
            + laterales
            + "</div></div>"
        )

    partes.extend(_fila_html(f, src, alt) for f in reparto.filas)
    return f'<div class="album" style="grid-template-rows:{filas}">' + "".join(partes) + "</div>"
