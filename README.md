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
| B | Crear, editar, borrador, buscar, duplicar, agrupación por VIN | Pendiente |
| C | Motor de plantillas, claves fijas en las 3 plantillas, vista previa real, validaciones | Pendiente |
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
│   ├── models.py        modelo de datos definitivo
│   ├── fields.py        claves fijas ← contrato con las plantillas
│   ├── locales.py       reglas por mercado y validaciones bancarias
│   ├── security.py      hashing, sesión y puerta de Configuración
│   ├── activity.py      registro de actividad
│   ├── seed.py          siembra inicial (datos de muestra)
│   ├── templates/       panel (Jinja2)
│   └── static/          CSS y JS del panel
├── templates_html/      LAS TRES PLANTILLAS APROBADAS (no tocar la maquetación)
├── alembic/             migraciones de base de datos
├── data/                base de datos, fotos y snapshots (volumen)
└── verificar_fase_a.py  comprobación de punta a punta
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
```

31 comprobaciones. No mira que las páginas «carguen», mira que hagan lo que
tienen que hacer: que la contraseña incorrecta no entre, que Configuración no se
pueda abrir sin la Master Password, que los datos bancarios no aparezcan en el
HTML mientras está bloqueada, que el bloqueo vuelva a cerrarse al salir, que la
búsqueda filtre de verdad y que cada acción quede registrada.

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
