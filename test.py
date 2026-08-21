import json
with open("reglas_cuentas.json", encoding="utf-8") as f:
    reglas = json.load(f)
for cat, d in reglas.items():
    print(f"{cat}: {len(d)} reglas")