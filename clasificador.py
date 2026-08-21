import json
import re
import unicodedata
from pathlib import Path
import math
import pandas as pd

ARCHIVO_REGLAS = Path("reglas_cuentas.json")
CATEGORIAS = ["ACTIVO", "PASIVO", "GASTO", "INGRESO"]

# Nombre de columna en tu planilla -> categoría del diccionario
COL_A_CAT = {
    "ACTIVO": "ACTIVO",
    "PASIVO": "PASIVO",
    "PERDIDAS": "GASTO",
    "GANANCIAS": "INGRESO",
}


def normaliza(texto: str) -> str:
    texto = str(texto).lower().strip()
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


def to_num(valor) -> float:
    """Convierte valores vacíos o NaN a 0.0, y números tipo chileno a float."""
    if pd.isna(valor):
        return 0.0

    s = str(valor).strip()

    if s == "":
        return 0.0

    s_upper = s.upper()
    if s_upper in ("NAN", "NONE", "NULL"):
        return 0.0

    s = s.replace(".", "").replace(",", ".")

    try:
        num = float(s)
        if math.isnan(num) or math.isinf(num):
            return 0.0
        return num
    except ValueError:
        return 0.0


def cargar_reglas() -> dict:
    if ARCHIVO_REGLAS.exists():
        with open(ARCHIVO_REGLAS, encoding="utf-8") as f:
            reglas = json.load(f)
    else:
        reglas = {}

    for cat in CATEGORIAS:
        reglas.setdefault(cat, {})

    reglas.setdefault("REGLAS_DEPRECIACION_ACTIVO", {})
    return reglas


def guardar_reglas(reglas: dict) -> None:
    with open(ARCHIVO_REGLAS, "w", encoding="utf-8") as f:
        json.dump(reglas, f, ensure_ascii=False, indent=2)


def clasificar(descripcion: str, categoria: str, reglas: dict):
    """Devuelve (cuenta, palabra_clave) o None. Gana la coincidencia más larga."""
    d = normaliza(descripcion)
    candidatos = []
    for palabra, cuenta in reglas.get(categoria, {}).items():
        p = normaliza(palabra)
        if re.search(r"\b" + re.escape(p), d):
            candidatos.append((len(p), palabra, cuenta))
    if not candidatos:
        return None
    candidatos.sort(reverse=True)
    _, palabra, cuenta = candidatos[0]
    return cuenta, palabra


def agregar_regla(reglas, categoria, palabra, cuenta):
    categoria = categoria.upper()
    if categoria not in CATEGORIAS:
        raise ValueError(f"Categoría inválida: usa {CATEGORIAS}")
    reglas.setdefault(categoria, {})[palabra] = cuenta
    guardar_reglas(reglas)
    print(f"✓ [{categoria}] '{palabra}' -> '{cuenta}'")

def detectar_reclasificacion_depreciacion(descripcion: str, categoria: str, reglas: dict):
    desc = normaliza(descripcion)

    if categoria != "PASIVO":
        return None

    if "depreciacion acumulada" not in desc:
        return None

    mapa = reglas.get("REGLAS_DEPRECIACION_ACTIVO", {})

    for clave, cuenta_activo in sorted(mapa.items(), key=lambda x: len(normaliza(x[0])), reverse=True):
        if normaliza(clave) in desc:
            return {
                "categoria": "ACTIVO",
                "cuenta": cuenta_activo,
                "palabra": f"DEP_ACUM_{clave}"
            }

    return None

def aplicar_reclasificacion_depreciacion(montos: dict, reclasificacion: dict | None):
    finales = {
        "ACTIVO FINAL": montos.get("ACTIVO", 0.0),
        "PASIVO FINAL": montos.get("PASIVO", 0.0),
        "PERDIDAS FINAL": montos.get("PERDIDAS", 0.0),
        "GANANCIAS FINAL": montos.get("GANANCIAS", 0.0),
    }

    if not reclasificacion:
        return finales

    # Solo reclasificamos depreciación acumulada desde PASIVO a ACTIVO negativo
    if reclasificacion["categoria_final"] == "ACTIVO":
        monto_pasivo = montos.get("PASIVO", 0.0)
        finales["PASIVO FINAL"] = 0.0
        finales["ACTIVO FINAL"] = -abs(monto_pasivo)

    return finales

def procesar(archivo_entrada: str, archivo_salida: str, reglas: dict,
             col_cuentas: str = "CUENTAS"):
    """Lee la planilla, clasifica y aplica reclasificación especial de depreciación."""
    ruta = Path(archivo_entrada)
    if ruta.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(ruta, dtype=str)
    else:
        df = pd.read_csv(ruta, dtype=str, sep=None, engine="python")

    df.columns = [str(c).strip().upper() for c in df.columns]
    cols_monto = [c for c in COL_A_CAT if c in df.columns]

    if not cols_monto:
        raise ValueError(f"No encuentro columnas {list(COL_A_CAT)} en la planilla.")

    if col_cuentas.upper() not in df.columns:
        raise ValueError(f"No encuentro la columna '{col_cuentas}'. Columnas: {list(df.columns)}")

    resultados_cat = []
    resultados_cuenta = []
    resultados_palabra = []
    sin_clasificar = []

    nuevos_activo = []
    nuevos_pasivo = []
    nuevos_perdidas = []
    nuevos_ganancias = []

    for _, fila in df.iterrows():
        desc = str(fila[col_cuentas.upper()]).strip()
        montos = {c: to_num(fila[c]) for c in cols_monto}

        col_ganadora = max(montos, key=lambda c: abs(montos[c]))
        valor_ganador = montos[col_ganadora]

        # Caso sin movimiento
        if valor_ganador == 0:
            resultados_cat.append("")
            resultados_cuenta.append("SIN MONTO")
            resultados_palabra.append("")

            nuevos_activo.append(montos.get("ACTIVO", 0.0))
            nuevos_pasivo.append(montos.get("PASIVO", 0.0))
            nuevos_perdidas.append(montos.get("PERDIDAS", 0.0))
            nuevos_ganancias.append(montos.get("GANANCIAS", 0.0))
            continue

        cat = COL_A_CAT[col_ganadora]
        r = clasificar(desc, cat, reglas)

        if r:
            cuenta_base, palabra_base = r
        else:
            cuenta_base, palabra_base = "SIN CLASIFICAR", ""
            sin_clasificar.append(desc)

        # Valores por defecto, sin reclasificación
        categoria_final = cat
        cuenta_final = cuenta_base
        palabra_final = palabra_base

        activo_final = montos.get("ACTIVO", 0.0)
        pasivo_final = montos.get("PASIVO", 0.0)
        perdidas_final = montos.get("PERDIDAS", 0.0)
        ganancias_final = montos.get("GANANCIAS", 0.0)

        # Regla especial: depreciación acumulada -> activo negativo
        reclas = detectar_reclasificacion_depreciacion(desc, cat, reglas)
        if reclas:
            categoria_final = reclas["categoria"]
            cuenta_final = reclas["cuenta"]
            palabra_final = reclas["palabra"]

            monto_pasivo = montos.get("PASIVO", 0.0)
            activo_final = -abs(monto_pasivo)
            pasivo_final = 0.0
            perdidas_final = 0.0
            ganancias_final = 0.0

        resultados_cat.append(categoria_final)
        resultados_cuenta.append(cuenta_final)
        resultados_palabra.append(palabra_final)

        nuevos_activo.append(activo_final)
        nuevos_pasivo.append(pasivo_final)
        nuevos_perdidas.append(perdidas_final)
        nuevos_ganancias.append(ganancias_final)

    # Sobrescribimos columnas originales
    df["ACTIVO"] = nuevos_activo
    df["PASIVO"] = nuevos_pasivo
    df["PERDIDAS"] = nuevos_perdidas
    df["GANANCIAS"] = nuevos_ganancias
    df["CATEGORIA"] = resultados_cat
    df["CUENTA CONTABLE"] = resultados_cuenta
    df["PALABRA CLAVE"] = resultados_palabra

    # Dejamos solo las columnas finales que quieres
    columnas_salida = [
        "CUENTAS", "ACTIVO", "PASIVO", "PERDIDAS", "GANANCIAS",
        "CATEGORIA", "CUENTA CONTABLE", "PALABRA CLAVE"
    ]

    for col in columnas_salida:
        if col not in df.columns:
            df[col] = ""

    df = df[columnas_salida]

    salida = Path(archivo_salida)
    if salida.suffix.lower() in (".xlsx", ".xls"):
        df.to_excel(salida, index=False)
    else:
        df.to_csv(salida, index=False)

    total = len(df)
    ok = sum(1 for x in resultados_cuenta if x not in ("SIN CLASIFICAR", "SIN MONTO"))
    print(f"✓ '{archivo_entrada}' -> '{archivo_salida}'")
    print(f"  {ok}/{total} clasificadas correctamente.")

    if sin_clasificar:
        print(f"  ⚠ {len(sin_clasificar)} sin clasificar (agrégales una regla):")
        for d in sin_clasificar[:20]:
            print(f"     - {d}")

    return df

def menu():
    reglas = cargar_reglas()
    while True:
        print("\n=== CLASIFICADOR DE CUENTAS ===")
        print("1) Procesar planilla (Excel/CSV)")
        print("2) Agregar una regla nueva")
        print("3) Probar una descripción suelta")
        print("4) Reglas por categoría")
        print("0) Salir")
        op = input("Opción: ").strip()
        if op == "1":
            ent = input("Archivo entrada (.xlsx/.csv): ").strip()
            sal = input("Archivo salida (.xlsx/.csv): ").strip()
            procesar(ent, sal, reglas)
        elif op == "2":
            cat = input(f"Categoría {CATEGORIAS}: ")
            pal = input("Palabra clave: ").strip()
            cta = input("Cuenta contable: ").strip()
            agregar_regla(reglas, cat, pal, cta)
        elif op == "3":
            cat = input(f"Categoría {CATEGORIAS}: ").upper()
            desc = input("Descripción: ")
            r = clasificar(desc, cat, reglas)
            print("->", r if r else "SIN CLASIFICAR")
        elif op == "4":
            for c in CATEGORIAS:
                print(f"  {c}: {len(reglas.get(c, {}))} reglas")
        elif op == "0":
            break
        else:
            print("Opción no válida.")


if __name__ == "__main__":
    menu()