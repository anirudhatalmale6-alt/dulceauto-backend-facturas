"""Recuentos de una base de DulceAuto, para el inventario del punto de recuperacion.

    python3 despliegue/recuentos.py data/dulceauto.db

Se usa dos veces y ahi esta la gracia: una sobre la base de produccion y otra
sobre la base sacada de DENTRO de la copia. Si las dos salidas no son identicas,
la copia no sirve para restaurar, por muy bien que pese.

Va en un archivo y no incrustado en el script de shell porque incrustarlo
obligaba a escapar comillas dentro de comillas y la primera version se rompio
justo ahi.
"""
import sqlite3
import sys


def recuentos(ruta: str) -> str:
    c = sqlite3.connect(ruta)
    q = lambda s: c.execute(s).fetchone()[0]  # noqa: E731
    lineas = [
        f"facturas           {q('select count(*) from invoice')}",
        f"snapshots totales  {q('select count(*) from invoice_snapshot')}",
    ]
    for tipo, n in c.execute(
        "select doc_type, count(*) from invoice_snapshot group by doc_type order by doc_type"
    ):
        lineas.append(f"   {tipo:<16} {n}")
    lineas += [
        f"fotografias        {q('select count(*) from invoice_photo')}",
        f"perfiles de marca  {q('select count(*) from brand_profile')}",
        f"ajustes            {q('select count(*) from setting')}",
        f"guia Call Center   {q('select count(*) from operator_faq')}",
        f"notas              {q('select count(*) from operator_note')}",
        f"folios registrados {q('select count(*) from folio_ledger')}",
        f"alembic            {q('select version_num from alembic_version')}",
    ]
    # La huella de los datos bancarios: cuantos ajustes y cuantos caracteres
    # suman. Es la comprobacion que venimos usando para poder afirmar que no se
    # ha movido ni un digito de una CLABE. Sin chr(), que esta build de SQLite
    # no lo trae.
    n, total = c.execute(
        "select count(*), sum(length(value)) from setting where key like 'banking.%'"
    ).fetchone()
    lineas.append(f"huella bancaria    {n}/{total}")
    lineas.append(f"integridad         {q('pragma integrity_check')}")
    return "\n".join(lineas)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Uso: recuentos.py <ruta de la base>")
    print(recuentos(sys.argv[1]))
