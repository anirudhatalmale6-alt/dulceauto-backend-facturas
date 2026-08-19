"""
Comprobacion de las reglas de datos: formato de importes y validaciones
bancarias.

Se ejecuta sin servidor y sin base de datos:

    python verificar_datos.py
"""
import sys

from app.locales import format_amount, validate_cbu, validate_clabe, validate_vin

ok, fallos = 0, []


def check(nombre, obtenido, esperado):
    global ok
    if obtenido == esperado:
        ok += 1
        print(f"  OK    {nombre}")
    else:
        fallos.append(nombre)
        print(f"  FALLA {nombre}\n         esperaba {esperado!r}\n         obtuvo   {obtenido!r}")


print("\nFormato de importes por mercado")
check("es-MX usa coma para miles", format_amount(329000, "es-MX"), "$329,000.00 MXN")
check("en usa el formato mexicano", format_amount(329000, "en"), "$329,000.00 MXN")
check("es-AR invierte los separadores", format_amount(329000, "es-AR"), "$329.000,00 ARS")
check("es-AR con importe pequeno", format_amount(3240, "es-AR"), "$3.240,00 ARS")
check("sin moneda cuando se pide", format_amount(329000, "es-AR", False), "$329.000,00")
check("millones se agrupan bien", format_amount(1234567.5, "es-MX"), "$1,234,567.50 MXN")
check("vacio si no hay importe", format_amount(None, "es-MX"), "")

print("\nCLABE mexicana (18 digitos)")
check("CLABE valida", validate_clabe("012180001234567899")[0], True)
check("un digito cambiado se detecta", validate_clabe("012180001234567898")[0], False)
check("17 digitos se rechazan", validate_clabe("01218000123456789")[0], False)
check("los espacios no molestan", validate_clabe("0121 8000 1234 5678 99")[0], True)

print("\nCBU argentino (22 digitos)")
check("CBU valido", validate_cbu("2850590994009041813526")[0], True)
check("primer bloque alterado se detecta", validate_cbu("2851590994009041813526")[0], False)
check("segundo bloque alterado se detecta", validate_cbu("2850590994009041813527")[0], False)
check("21 digitos se rechazan", validate_cbu("285059099400904181352")[0], False)

print("\nVIN")
check("VIN valido de 17", validate_vin("19UTC2895KL500992")[0], True)
check("16 caracteres se rechazan", validate_vin("19UTC2895KL50099")[0], False)
check("la letra O se rechaza", validate_vin("19UTC2895KL5009O2")[0], False)
check("la letra I se rechaza", validate_vin("19UTC2895KL5009I2")[0], False)
check("minusculas se aceptan", validate_vin("19utc2895kl500992")[0], True)

print(f"\n{'=' * 58}\n{ok} comprobaciones correctas, {len(fallos)} fallos")
sys.exit(1 if fallos else 0)
