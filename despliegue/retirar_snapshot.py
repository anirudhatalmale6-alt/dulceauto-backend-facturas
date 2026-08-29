"""Retirar documentos concretos del historico.

    python despliegue/retirar_snapshot.py 13 12 --motivo "prueba anterior a la regla"
    python despliegue/retirar_snapshot.py 13 12 --motivo "..." --hacerlo

Sin --hacerlo NO borra nada: enseña exactamente lo que tocaria y para. Se mira
primero y se ejecuta despues, que es lo unico que evita borrar lo que no era.

Por que un script y no unos comandos sueltos
--------------------------------------------
Un borrado en produccion tiene que poder repasarse antes y explicarse despues.
Aqui las dos cosas quedan por escrito: que filas, que carpetas, que habia antes,
que hay despues, y una entrada en Actividad diciendo por que se retiro.

Lo que comprueba antes de borrar un archivo
-------------------------------------------
La ruta que hay en la base es un DATO, no una promesa: puede apuntar a
cualquier sitio si alguien la edito. Antes de tocar el disco se comprueba que la
carpeta resuelta cae DENTRO de data/snapshots y que su nombre es el que le
corresponde a esa factura, ese tipo y esa version. Si no cuadra, no se borra y
se dice.

La pre-factura NUNCA se retira desde aqui. Es el documento legal de la
operacion; si algun dia hiciera falta, sera con otra conversacion y otro script.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from app import activity as act
from app import doctypes
from app import pdf as pdf_engine
from app.config import settings
from app.db import SessionLocal
from app.models import ActivityLog, Invoice, InvoiceSnapshot, utcnow


def recuentos(db) -> dict:
    from sqlalchemy import func, select

    filas = db.execute(
        select(InvoiceSnapshot.doc_type, func.count()).group_by(InvoiceSnapshot.doc_type)
    ).all()
    return {
        "facturas": db.query(Invoice).count(),
        "snapshots": db.query(InvoiceSnapshot).count(),
        **{t: n for t, n in filas},
    }


def carpeta_de(s: InvoiceSnapshot) -> Path | None:
    """Carpeta del snapshot, comprobada. None si no cuadra con lo esperado."""
    esperada = pdf_engine.carpeta_snapshot(s.invoice_id, s.doc_type, s.version).resolve()
    raiz = settings.snapshots_dir.resolve()
    if not esperada.is_relative_to(raiz):
        return None

    # Y ademas, que sea la carpeta donde de verdad estan sus archivos: se
    # comprueba contra la ruta guardada, no solo contra la calculada.
    for rel in (s.pdf_path, s.html_path, s.assets_dir):
        if not rel:
            continue
        real = (settings.data_dir / rel).resolve()
        if not real.is_relative_to(raiz):
            return None
        # assets_dir cuelga de la carpeta; pdf y html estan dentro de ella.
        if esperada not in (real.parent, real, *real.parents):
            return None
    return esperada


def main() -> int:
    p = argparse.ArgumentParser(description="Retira snapshots del historico.")
    p.add_argument("ids", nargs="+", type=int, help="id de cada snapshot")
    p.add_argument("--motivo", required=True, help="queda escrito en Actividad")
    p.add_argument("--hacerlo", action="store_true", help="sin esto, solo enseña")
    args = p.parse_args()

    db = SessionLocal()
    antes = recuentos(db)
    print("RECUENTOS ANTES")
    for k, v in antes.items():
        print(f"   {k:<16} {v}")

    objetivos = []
    print("\nLO QUE SE VA A RETIRAR")
    for sid in args.ids:
        s = db.get(InvoiceSnapshot, sid)
        if s is None:
            print(f"   id {sid}: NO EXISTE. Se para aqui.")
            return 1
        if s.doc_type == doctypes.FACTURA:
            print(f"   id {sid}: es una PRE-FACTURA ({s.folio} v{s.version}). "
                  "Este script no las retira. Se para aqui.")
            return 1
        carpeta = carpeta_de(s)
        if carpeta is None:
            print(f"   id {sid}: las rutas guardadas no cuadran con su carpeta. Se para aqui.")
            return 1
        factura = db.get(Invoice, s.invoice_id)
        archivos = sorted(x for x in carpeta.rglob("*") if x.is_file()) if carpeta.exists() else []
        print(f"   id {sid}  {s.folio}  {doctypes.tipo(s.doc_type).nombre}  v{s.version}")
        print(f"      estado de la factura: {factura.status if factura else '?'}")
        print(f"      creado:               {s.created_at}")
        print(f"      carpeta:              {carpeta}")
        print(f"      archivos dentro:      {len(archivos)}")
        objetivos.append((s, carpeta, len(archivos)))

    # Lo que NO se toca, dicho antes de tocar nada.
    afectadas = {s.invoice_id for s, _, _ in objetivos}
    print("\nLO QUE NO SE TOCA")
    for inv_id in sorted(afectadas):
        otros = [
            x for x in db.query(InvoiceSnapshot).filter(InvoiceSnapshot.invoice_id == inv_id).all()
            if x.id not in args.ids
        ]
        f = db.get(Invoice, inv_id)
        print(f"   {f.folio}: se conservan {len(otros)} version(es)")
        for x in sorted(otros, key=lambda x: (x.doc_type, x.version)):
            print(f"      {doctypes.tipo(x.doc_type).nombre} v{x.version}")

    if not args.hacerlo:
        print("\nEnsayo. No se ha borrado nada. Repite con --hacerlo para ejecutarlo.")
        return 0

    print("\nRETIRANDO")
    for s, carpeta, n in objetivos:
        folio, tipo_clave, version = s.folio, s.doc_type, s.version
        invoice_id = s.invoice_id
        if carpeta.exists():
            shutil.rmtree(carpeta)
            print(f"   borrada la carpeta {carpeta} ({n} archivos)")
        else:
            print(f"   la carpeta {carpeta} ya no estaba")
        # Al quitar la version se queda vacia la carpeta del tipo, y a veces la
        # de la factura entera. Una carpeta vacia tambien es un resto: se sube
        # borrando lo que queda vacio, SIN pasar nunca de data/snapshots.
        raiz = settings.snapshots_dir.resolve()
        padre = carpeta.parent
        while padre.resolve() != raiz and padre.resolve().is_relative_to(raiz):
            if padre.exists() and not any(padre.iterdir()):
                padre.rmdir()
                print(f"   borrada la carpeta vacia {padre}")
                padre = padre.parent
            else:
                break
        db.delete(s)
        db.add(ActivityLog(
            action=act.SNAPSHOT_REMOVED,
            actor="Admin",
            entity_type="invoice",
            entity_id=invoice_id,
            folio=folio,
            detail=f"{doctypes.tipo(tipo_clave).nombre} v{version} · {args.motivo}",
            created_at=utcnow(),
        ))
        print(f"   retirada la fila {folio} {tipo_clave} v{version} y anotada en Actividad")
    db.commit()

    despues = recuentos(db)
    print("\nRECUENTOS DESPUES")
    for k in sorted(set(antes) | set(despues)):
        a, d = antes.get(k, 0), despues.get(k, 0)
        print(f"   {k:<16} {a} -> {d}" + ("" if a == d else f"   ({d - a:+d})"))

    print("\nCOMPROBACIONES")
    # 1 · las carpetas ya no estan
    quedan = [str(c) for _, c, _ in objetivos if c.exists()]
    print(f"   carpetas borradas de verdad: {'si' if not quedan else 'NO: ' + ', '.join(quedan)}")

    # 2 · no queda ninguna carpeta sin su fila (huerfana)
    vivas = {
        pdf_engine.carpeta_snapshot(x.invoice_id, x.doc_type, x.version).resolve()
        for x in db.query(InvoiceSnapshot).all()
    }
    huerfanas = []
    raiz = settings.snapshots_dir
    if raiz.exists():
        for carpeta_inv in raiz.iterdir():
            if not carpeta_inv.is_dir():
                continue
            for d in carpeta_inv.rglob("v*"):
                if d.is_dir() and d.resolve() not in vivas:
                    huerfanas.append(str(d))
    print(f"   carpetas huerfanas: {len(huerfanas)}")
    for h in huerfanas:
        print(f"      {h}")

    vacias = [str(d) for d in raiz.rglob("*") if d.is_dir() and not any(d.iterdir())] if raiz.exists() else []
    print(f"   carpetas vacias: {len(vacias)}")
    for v in vacias:
        print(f"      {v}")

    # 3 · ninguna fila apunta a un archivo que ya no existe
    rotas = [
        f"{x.folio} {x.doc_type} v{x.version}"
        for x in db.query(InvoiceSnapshot).all()
        if x.pdf_path and not (settings.data_dir / x.pdf_path).exists()
    ]
    print(f"   filas que apuntan a un PDF que no esta: {len(rotas)}")
    for r in rotas:
        print(f"      {r}")

    # 4 · la siguiente version de cada tipo afectado
    print("\n   SIGUIENTE VERSION QUE SE EMITIRIA")
    for inv_id in sorted(afectadas):
        f = db.get(Invoice, inv_id)
        for clave in (doctypes.FACTURA, *doctypes.COMPLEMENTARIOS):
            print(f"      {f.folio}  {doctypes.tipo(clave).nombre:<28} "
                  f"v{pdf_engine._siguiente_version(db, inv_id, clave)}")

    return 1 if (quedan or huerfanas or rotas or vacias) else 0


if __name__ == "__main__":
    sys.exit(main())
