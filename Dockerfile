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
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
