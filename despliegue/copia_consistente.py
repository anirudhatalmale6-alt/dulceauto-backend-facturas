"""Copia consistente de la base, para el punto de recuperacion.

    python3 despliegue/copia_consistente.py data/dulceauto.db /destino/dulceauto.db

Por que NO vale copiar el archivo con tar
-----------------------------------------
La base va en modo WAL: las escrituras recientes viven en dulceauto.db-wal y no
estan todavia dentro del .db. Copiando solo el .db se pierden; copiando los tres
archivos con el sistema en marcha se pueden coger en un momento intermedio.

Se vio midiendo, no razonando: el primer ensayo del punto de recuperacion dio
337 facturas dentro de la copia contra 350 en produccion. Trece facturas
perdidas en una copia que pesaba lo que tenia que pesar y se abria sin errores.

sqlite3.Connection.backup() usa la API de copia de SQLite: bloquea lo justo,
integra el WAL y deja UN archivo consistente, con el sistema funcionando.
"""
import sqlite3
import sys
from pathlib import Path


def copiar(origen: str, destino: str) -> tuple[int, int]:
    """Devuelve (filas de invoice en el origen, en el destino) para comprobarlo."""
    Path(destino).parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{origen}?mode=ro", uri=True)
    dst = sqlite3.connect(destino)
    with dst:
        src.backup(dst)
    n_src = src.execute("select count(*) from invoice").fetchone()[0]
    n_dst = dst.execute("select count(*) from invoice").fetchone()[0]
    # La copia sale sin WAL: un solo archivo que se restaura tal cual.
    dst.execute("pragma journal_mode=delete")
    dst.close()
    src.close()
    return n_src, n_dst


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Uso: copia_consistente.py <base> <destino>")
    origen, destino = sys.argv[1], sys.argv[2]
    n_src, n_dst = copiar(origen, destino)
    print(f"facturas en origen {n_src}, en la copia {n_dst}")
    if n_src != n_dst:
        raise SystemExit("la copia no tiene las mismas filas que el origen")
