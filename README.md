# DulceAuto · Backend Facturas Premium V1

Backend interno para crear, editar y generar las pre-facturas Premium en sus
tres versiones aprobadas: **es-MX**, **English** y **es-AR**.

Un solo backend sirve los tres mercados. Las plantillas HTML son exactamente las
que se aprobaron en el Milestone 1: no se han vuelto a maquetar ni se han
tocado sus estilos.

---

## Estado por fases

| Fase | Contenido | Estado |
|------|-----------|--------|
| **A** | Base del proyecto, acceso, Master Password, modelo de datos, panel con las 6 vistas y los 3 modos visuales | **Entregada** |
| **B** | Crear, editar, borrador, buscar, duplicar, agrupación por VIN | **Entregada** |
| **C** | Motor de plantillas, claves fijas en las 3 plantillas, reglas por mercado, vista previa real | **Entregada** |
| D | Generación PDF A4, snapshot histórico, logo, QR, código de barras, actividad, instalación | Pendiente |

Donde una acción todavía no está cableada, la pantalla lo indica con una
etiqueta de fase en lugar de ofrecer un botón que no hace nada.

---

## Puesta en marcha

### Con Docker, que es como irá en el servidor

```bash
cp .env.example .env      # y cambiar la clave y las dos contraseñas
docker compose up -d --build
```

Queda escuchando en `127.0.0.1:8000`. Delante va nginx o Traefik con el
certificado. **No exponer el puerto 8000 directamente a internet**: el panel
viajaría sin cifrar.

### En local, sin Docker

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp .env.example .env
./.venv/bin/alembic upgrade head
./.venv/bin/uvicorn app.main:app --port 8000
```

### Credenciales iniciales

Las de `.env.example`: usuario `admin`, contraseña `DulceAuto2026`, Master
Password `Master2026`. Se siembran **hasheadas** la primera vez que arranca y
después ya no se leen de ahí.

El panel avisa en todas las pantallas mientras sigan sin cambiarse. Se cambian
desde Configuración, que a su vez exige la Master Password.

---

## Las dos contraseñas

Son barreras independientes, tal y como se pidió:

1. **Contraseña del panel.** Cuenta única y compartida. Da acceso al backend.
2. **Master Password.** Solo para Configuración. Se pide **aunque la sesión del
   panel ya esté abierta**.

Configuración arranca siempre bloqueada. Se vuelve a bloquear sola tras
15 minutos sin actividad (`MASTER_SESSION_MINUTES`) y al cerrar sesión.

Ninguna de las dos aparece nunca en el HTML ni en el JavaScript. Se guardan
hasheadas con **scrypt**, con sal distinta en cada una, y se comprueban siempre
en el servidor. La comparación es en tiempo constante, para no filtrar
información por lo que tarda en responder.

Se eligió scrypt, de la librería estándar de Python, en lugar de argon2 o
bcrypt: no añade ninguna dependencia que haya que compilar en el servidor y para
un panel interno la diferencia real de seguridad es nula.

---

## Estructura

```
backend/
├── app/
│   ├── main.py          rutas y vistas
│   ├── invoices.py      crear, editar, duplicar, agrupar por VIN
│   ├── documents.py     motor de plantillas ← rellena las 3 aprobadas
│   ├── models.py        modelo de datos definitivo
│   ├── fields.py        claves fijas ← contrato con las plantillas
│   │                    y qué se copia al duplicar
│   ├── locales.py       reglas por mercado y validaciones bancarias
│   ├── security.py      hashing, sesión y puerta de Configuración
│   ├── activity.py      registro de actividad
│   ├── seed.py          siembra inicial (datos de muestra)
│   ├── templates/       panel (Jinja2)
│   └── static/          CSS y JS del panel
├── templates_html/      LAS TRES PLANTILLAS APROBADAS (no tocar la maquetación)
│   ├── aprobado-original/  copia intacta, para poder comparar contra ella
│   └── marcar_campos.py    qué atributos se les añadieron, y solo atributos
├── alembic/             migraciones de base de datos
├── data/                base de datos, fotos y snapshots (volumen)
├── verificar_fase_a.py     acceso, Master Password, vistas y temas
├── verificar_fase_b.py     crear, editar, borrador, duplicar, VIN
├── verificar_fase_c.py     vista previa real y plantillas en el navegador
├── verificar_plantillas.py el motor de plantillas (sin servidor)
├── verificar_folios.py     contador y choque de folios (sin servidor)
└── verificar_datos.py      importes, CLABE, CBU y VIN (sin servidor)
```

---

## Claves fijas

Sustituyen definitivamente a las claves derivadas del texto que se usaron en el
Milestone 1, donde cambiar una frase en es-MX renombraba la clave y
desenganchaba en silencio el inglés y el es-AR.

La regla es simple y no tiene excepciones: la clave es `grupo.campo` y la
columna es `grupo_campo`.

| Clave fija | Columna |
|---|---|
| `customer.name` | `Invoice.customer_name` |
| `transaction.folio` | `Invoice.folio` |
| `vehicle.vin` | `Invoice.vehicle_vin` |
| `pricing.vehicle_price` | `Invoice.pricing_vehicle_price` |
| `payment.amount` | `Invoice.pricing_reservation_amount` |
| `payment.account` | `Invoice.banking_account_number` |
| `delivery.date` | `Invoice.delivery_date` |
| `representative.name` | `Invoice.representative_name` |
| `template.locale` | `Invoice.locale` |

El mapa completo está en `app/fields.py`.

A partir de aquí se puede cambiar cualquier texto de la factura sin romper
ningún idioma.

---

## Motor de plantillas (Fase C)

`app/documents.py` coge una factura y una de las tres plantillas aprobadas y
devuelve el documento con los datos puestos. La regla que manda sobre todas las
demás: **el diseño no se toca**.

### Cómo está hecho, y por qué así

No se construye un árbol DOM ni se vuelve a serializar el HTML. Un árbol hay que
volver a escribirlo, y al escribirlo se normalizan comillas, espacios, saltos de
línea y etiquetas vacías: el archivo saldría distinto del aprobado aunque el
navegador lo pintara parecido.

Aquí se hace al revés. Se recorre el HTML con `html.parser` **solo para anotar
posiciones**, y después se cortan y pegan trozos del archivo original. Todo lo
que no sea un hueco marcado sale byte a byte como estaba.

Eso permite una comprobación que no depende de mirar capturas: se monta una
factura con los mismos datos que lleva la versión aprobada y se exige que el
documento generado sea **idéntico byte a byte** al archivo aprobado. Si el motor
cambiara un espacio, esa comprobación falla. Está en `verificar_plantillas.py` y
se hace para los tres mercados.

### Qué se le añadió a las plantillas aprobadas

Atributos, y nada más:

| Atributo | Para qué |
|---|---|
| `data-field="..."` | ese hueco lleva un dato de la factura |
| `data-step="1..4"` | paso de la barra de progreso, para marcarlo según el estado |
| `data-hide-if-empty="..."` | el elemento desaparece si ese dato está vacío (la pastilla de descuento) |

`templates_html/marcar_campos.py` deja por escrito exactamente qué se añadió, y
`templates_html/aprobado-original/` guarda las tres plantillas tal y como se
aprobaron. La comprobación quita esos atributos del archivo marcado y exige que
el resultado sea idéntico al original.

Son 41 huecos por plantilla, y los mismos 41 en las tres.

### Lo que decide el backend, y lo que no

| Decide el backend | Es fijo de la plantilla |
|---|---|
| El contenido de los 41 huecos | Textos legales, FAQ, protección, documentación |
| El formato de fechas e importes del mercado | Toda la maquetación y el CSS |
| Qué paso de la barra de progreso está activo | Los nombres de los cuatro pasos |
| Cuál de las dos modalidades de entrega va primero | Los textos de las dos modalidades |
| Si la pastilla de descuento se ve | El diseño de la pastilla |

### Reglas por mercado

| | es-MX | English | es-AR |
|---|---|---|---|
| Fecha de emisión | `22 Jul 2026` | `22 Jul 2026` | `22 Jul 2026` |
| Fecha de entrega | `27 de julio de 2026` | `27 July 2026` | `27 de julio de 2026` |
| Vigencia | `29/07/2026` | `29/07/2026` | `29/07/2026` |
| Importe | `$329,000.00 MXN` | `$329,000.00 MXN` | `$329.000,00 ARS` |
| Cuenta | CLABE, 18 dígitos | CLABE, 18 dígitos | CBU, 22 dígitos |

Los nombres de los meses están escritos en `app/locales.py` y no se piden al
sistema: `strftime` depende del idioma instalado en la máquina, y un servidor sin
español configurado devolvería «July» en la factura mexicana sin avisar de nada.

### El estado que ve el cliente

El estado del panel y el del documento no son el mismo. «PDF generado» y
«Enviada» son estados internos de gestión: que hayamos generado el PDF no
significa que el cliente haya pagado, así que el documento sigue diciendo «Pago
pendiente» y la barra de progreso no avanza.

| Estado en el panel | En el documento | Barra de progreso |
|---|---|---|
| Borrador | Borrador / Draft | paso 1 activo |
| Pago pendiente | Pago pendiente / Payment pending | paso 1 hecho, paso 2 activo |
| PDF generado | Pago pendiente / Payment pending | igual que el anterior |
| Enviada | Pago pendiente / Payment pending | igual que el anterior |
| Cancelada | Cancelada / Cancelled | paso 1 hecho, ninguno activo |

### Vista previa

`/facturas/{id}/vista-previa` enseña el documento dentro de un iframe que apunta
a `/facturas/{id}/documento`, que es la misma URL que se imprime y la que usará
el generador de PDF de la Fase D. No se copia el HTML dentro del panel: si se
copiara, el CSS del panel podría cambiar el aspecto de la factura y la vista
previa dejaría de ser prueba de nada. Una de las comprobaciones verifica
justamente que dentro del documento no haya ni rastro del CSS del panel.

El iframe se pinta siempre a 900 px, que es el ancho de diseño de la página A4, y
se reduce con `transform: scale()`. Estrechar el iframe sería más fácil, pero el
CSS aprobado tiene puntos de ruptura para móvil y el documento cambiaría de
maquetación.

### Lo que falta se queda en blanco

Un hueco sin dato sale vacío; nunca se queda el texto de ejemplo de la maqueta.
La pantalla de vista previa lista los datos que faltan por su nombre.

---

## Decisiones del modelo de datos

Tres, y conviene no deshacerlas, porque son las que mantienen viva la promesa de
poder migrar a MySQL o PostgreSQL sin reescribir la aplicación:

1. **Fechas en UTC y sin zona horaria.** sqlite no conserva la zona, así que
   guardarla daría un comportamiento distinto en cada motor. La conversión a
   hora local se hace al mostrar.
2. **Sin tipos ENUM.** Cada motor los implementa a su manera y modificarlos
   después es doloroso. Los estados y los idiomas son texto validado en la
   aplicación.
3. **Importes en `Numeric(14,2)`, no en coma flotante.**

Además, todo el acceso pasa por SQLAlchemy: no hay una sola línea de SQL escrita
a mano ni ningún tipo propio de sqlite. Migrar es cambiar `DATABASE_URL` y
correr las migraciones.

### Datos bancarios congelados en la factura

Los datos bancarios y los del representante se **copian** a la factura al
crearla, en lugar de leerse de Configuración cada vez que se muestra.

Es a propósito: si mañana se cambia la cuenta en Configuración, una factura ya
emitida tiene que seguir enseñando la cuenta a la que se le pidió pagar al
cliente. Lo mismo se aplicará al snapshot y a sus activos en la Fase D.

---

## Duplicar: qué se copia y qué no

La regla está declarada en `app/fields.py`, en `DUPLICATE_CARRY_FIELDS`, y no
repartida por el código. Todo lo que no esté en esa lista se reinicia en la
copia.

| Se copia | Se reinicia |
|---|---|
| Vehículo completo y VIN | Datos del cliente original: nombre, email, teléfono, ciudad (la pantalla de duplicar pide los del nuevo interesado) |
| Precios, descuento, seguro, transporte | Folio (se genera uno nuevo) |
| Plantilla y moneda | Fecha de emisión, vigencia, entrega |
| Modalidad y textos de entrega | Autorización |
|  | Estado: la copia **nace siempre como borrador** |

**Duplicar no confirma la reserva.** La copia nace siempre en borrador, sin
excepciones: no hay ninguna opción en pantalla para elegir otro estado, y un
envío manipulado que intente forzarlo tampoco lo consigue. Cuando la copia esté
completa se pasa a «Pago pendiente» desde el editor, que es donde se validan los
campos obligatorios.

### Los datos bancarios de una copia son los de hoy

Banco, beneficiario, CLABE/CBU y representante **no se copian del original**: se
cargan de la Configuración vigente del mercado en el momento de crear la copia.

Las dos mitades de esta regla importan y tiran en direcciones distintas:

- Una factura **ya emitida** conserva para siempre la cuenta a la que se pidió
  pagar al cliente, aunque después se cambie en Configuración.
- Una **copia nueva** es una operación nueva para un cliente nuevo, así que
  nunca arrastra una cuenta que ya se haya cambiado.

Cuando un vehículo ya tiene alguna factura en estado avanzado (PDF generado o
enviada), el editor, la pantalla de duplicar y el historial del vehículo lo
avisan. Es un aviso, no un bloqueo: varias pre-facturas por VIN son
precisamente el caso de varios interesados.

---

## Borradores y campos obligatorios

Un borrador se guarda a medias, que es para lo que sirve. Para que una factura
deje de ser borrador se exigen: cliente, vehículo, VIN válido, precio, importe
de pre-reserva y fecha de emisión. Si falta algo, el panel lo dice campo por
campo y **no se guarda nada**, conservando lo que el operador acababa de
escribir en pantalla.

También se comprueba que la vigencia y la fecha de entrega no sean anteriores a
la de emisión.

### El folio

Lo asigna siempre el contador de Configuración (`folio.prefix` y `folio.next`) y
**no se edita a mano**. En una factura nueva el campo muestra «Automático» y una
vez creada queda de solo lectura. No está en `EDITABLE_FIELDS`, así que un envío
manipulado tampoco lo cambia.

Es a propósito: la referencia bancaria se genera a partir del folio, y un folio
editado a mano dejaría la factura y la referencia diciendo cosas distintas.

**Uso simultáneo.** La cuenta de Admin es compartida, así que dos operadores
pueden crear una factura casi a la vez y el segundo encontrarse el folio ya
ocupado. `commit_creation()` captura ese choque de clave única, deshace la
transacción y vuelve a intentarlo con el siguiente folio libre, en lugar de
enseñar un error de base de datos. Solo se reintenta el choque de folio: un
`NOT NULL` o cualquier otro error de integridad sale a la superficie, porque
reintentarlo a ciegas solo serviría para esconderlo.

### Importes escritos a mano

El mismo panel sirve a México y a Argentina, así que se aceptan las dos
escrituras: `412.500,00` y `412,500.00` se guardan como el mismo número. Debajo
del campo se muestra cómo quedará escrito en la factura de ese mercado.

---

## Agrupación por VIN

`/vehiculos` lista un registro por número de bastidor y `/vehiculos/{VIN}`
enseña el historial completo de ese coche, con todas sus pre-facturas, sus
clientes y sus estados. El editor muestra también las demás facturas del mismo
VIN.

Se agrupa por VIN y no por el título del vehículo porque el bastidor identifica
al coche y no cambia, mientras que el título es texto libre y se escribe
distinto cada vez.

---

## Validaciones bancarias

Comprobar la longitud detecta un número incompleto, pero no un dígito mal
tecleado. Los dos formatos llevan dígitos de control precisamente para eso:

- **CLABE** (México, 18 dígitos): ponderación 3-7-1 sobre los 17 primeros.
- **CBU** (Argentina, 22 dígitos): dos bloques, cada uno con su dígito de
  control.
- **VIN**: 17 caracteres, sin `I`, `O` ni `Q`, que el estándar excluye para que
  no se confundan con 1 y 0.

Así el error se caza en el panel y no cuando el cliente intenta transferir.

---

## Formato de importes

No es un detalle menor: México escribe `329,000.00` y Argentina `329.000,00`.
Con los separadores invertidos, el mismo número se lee como dos cantidades muy
distintas.

| Mercado | Resultado |
|---|---|
| es-MX | `$329,000.00 MXN` |
| en | `$329,000.00 MXN` |
| es-AR | `$329.000,00 ARS` |

---

## Comprobación

```bash
python3 verificar_fase_a.py http://127.0.0.1:8000 /tmp/capturas
python3 verificar_fase_b.py http://127.0.0.1:8000 /tmp/capturas
python3 verificar_fase_c.py http://127.0.0.1:8000 /tmp/capturas
python3 verificar_plantillas.py
python3 verificar_folios.py
python3 verificar_datos.py
```

**243 comprobaciones en total.** No miran que las páginas «carguen», miran que
hagan lo que tienen que hacer. Se pueden ejecutar tantas veces seguidas como se
quiera: la de Fase B borra al arrancar las facturas que dejó la ejecución
anterior, para que los recuentos por VIN sigan significando algo.

- **Fase A · 32.** Que la contraseña incorrecta no entre, que Configuración no
  se pueda abrir sin la Master Password, que los datos bancarios no aparezcan
  en el HTML mientras está bloqueada, que el bloqueo vuelva a cerrarse al salir
  y que cada acción quede registrada.
- **Fase B · 64.** Que un borrador se guarde a medias pero no pueda salir de
  borrador con huecos, que un VIN inválido se rechace, que un intento fallido
  no borre lo tecleado ni gaste un folio, que la copia nazca en borrador y sin
  heredar los datos del cliente original, que use la cuenta bancaria vigente y no la del original, que la
  factura de origen no cambie al duplicarla, y que el agrupamiento por VIN
  cuente lo que tiene que contar.
- **Fase C · 57.** Con navegador: que la vista previa enseñe el documento real
  y no una imitación, que el CSS aprobado se cargue de verdad, que el del panel
  no se cuele dentro, que lo que se escribe en el editor salga en la factura,
  que el zoom no re-maquete el documento, que cambiar de mercado cambie de
  plantilla y de formatos, y que el documento no cambie con el tema del panel.
- **Plantillas · 59.** Sin navegador: el motor. Incluye la comprobación de que
  el documento generado con los datos de la versión aprobada es idéntico byte a
  byte al archivo aprobado, en los tres mercados.
- **Folios · 11.** El contador, el salto cuando un folio ya está ocupado y el
  reintento ante un choque simultáneo. Se ejecuta sin servidor, sobre una base
  de datos temporal, porque el choque entre dos operadores no se puede provocar
  desde el navegador.
- **Datos · 20.** Formatos de importe y dígitos de control de CLABE, CBU y VIN.
  También sin servidor.

Varias comprobaciones no se conforman con que la pantalla no ofrezca algo:
mandan la petición a mano para verificar que el servidor tampoco lo acepta. Es
el caso del folio y del estado al duplicar.

Las tres primeras dejan capturas en la carpeta indicada.

---

## Requisitos del servidor

VPS Linux con Docker. Mínimo recomendado: **2 GB de RAM, 2 vCPU y 20 GB de
disco**.

Chromium consume bastante memoria al generar el PDF; con 1 GB se queda corto en
cuanto haya dos generaciones a la vez.

`docker-compose.yml` sube `shm_size` a 512 MB. El valor por defecto de Docker es
64 MB y Chromium se queda sin memoria compartida al renderizar, fallando de
forma intermitente, que es la peor manera de fallar.

---

## Antes de poner en producción

- [ ] Cambiar `SECRET_KEY` en `.env` por una nueva:
      `python -c "import secrets; print(secrets.token_urlsafe(48))"`
- [ ] Cambiar las dos contraseñas desde Configuración
- [ ] Poner `https_only=True` en la cookie de sesión (`app/main.py`), una vez
      haya certificado
- [ ] Copia de seguridad periódica de `data/`
- [ ] Sustituir los datos bancarios de muestra por los reales
