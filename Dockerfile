# Imagen del backend de facturas.
#
# Se parte de la imagen oficial de Playwright porque trae Chromium ya instalado
# con todas sus librerias de sistema. Instalar Chromium a mano sobre una imagen
# limpia funciona, pero le faltan siempre dos o tres librerias graficas y el
# fallo aparece tarde: justo al generar el primer PDF.
#
# Es una imagen grande, alrededor de 1,5 GB. Es el precio de generar el PDF con
# el mismo motor con el que se aprobo el diseno.
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Las dependencias van en una capa aparte para que un cambio en el codigo no
# obligue a reinstalarlas en cada build.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY templates_html ./templates_html

# Commit del que se construyo esta imagen. Lo pone el script de despliegue con
# --build-arg. Sirve para poder comprobar, ANTES de ejecutar nada, que la imagen
# es la del codigo que hay en disco.
#
# Nace de dos errores del 29-ago-2026, el mismo los dos: ejecutar codigo nuevo
# dentro de la imagen anterior. La primera vez alembic dijo que habia migrado y
# no migro nada; la segunda, un script se corto a medias porque una constante
# nueva no existia dentro del contenedor.
ARG COMMIT=desconocido
ENV DULCEAUTO_COMMIT=$COMMIT

# La base de datos y los archivos subidos viven aqui. Es el unico directorio
# que hay que montar como volumen: sin eso, un redespliegue se lleva por
# delante las facturas.
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/salud', timeout=4).status==200 else 1)"

# Un solo worker a proposito. Chromium consume bastante memoria al generar el
# PDF y con 2 GB de RAM varios workers compiten por ella. Si el volumen crece,
# se sube aqui y se sube la RAM a la vez, no por separado.
#
# --forwarded-allow-ips es imprescindible detras de nginx. Sin el, uvicorn
# ignora la cabecera X-Forwarded-Proto que envia nginx, cree que la peticion
# llego por http y construye las direcciones de las hojas de estilo y del
# JavaScript como "http://...". El navegador, en una pagina https, bloquea ese
# contenido mixto: el panel se ve sin estilos y sin JavaScript.
#
# No vale con el valor por defecto -127.0.0.1-, porque nginx no entra por
# localhost sino por la pasarela de la red de Docker, con otra IP. Poner "*" es
# seguro aqui porque el puerto solo esta publicado en 127.0.0.1 del servidor:
# lo unico que puede llegar a uvicorn es el propio nginx.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
