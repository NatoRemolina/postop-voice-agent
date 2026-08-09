"""Orquestador ETL: extract -> transform -> load -> quality.

Uso (desde la raiz del repo, con el python3 del sistema):
    python3 -m scripts.run_etl
"""

import sqlite3
import sys

from etl.extract import (
    read_clinical_profiles,
    read_conversations,
    read_demographics,
    read_trajectories,
)
from etl.load import DEFAULT_WAREHOUSE_PATH, load_warehouse
from etl.quality import run_quality_checks
from etl.transform import (
    build_cases_master,
    transform_clinical_profiles,
    transform_conversations,
    transform_demographics,
    transform_trajectories,
)


def main() -> int:
    print("[1/4] Extract: leyendo los 4 archivos xlsx del dataset...")
    conversations = read_conversations()
    trajectories = read_trajectories()
    clinical_profiles = read_clinical_profiles()
    demographics = read_demographics()

    print("[2/4] Transform: JSON, llaves derivadas y maestro de casos...")
    conversations = transform_conversations(conversations)
    trajectories = transform_trajectories(trajectories)
    clinical_profiles = transform_clinical_profiles(clinical_profiles)
    demographics = transform_demographics(demographics)
    cases = build_cases_master(conversations, trajectories, clinical_profiles, demographics)
    frames = {
        "pacientes": demographics,
        "perfiles_clinicos": clinical_profiles,
        "trayectorias": trajectories,
        "casos": cases,
        "turnos": conversations,
    }

    print(f"[3/4] Load: escribiendo {DEFAULT_WAREHOUSE_PATH}...")
    counts = load_warehouse(DEFAULT_WAREHOUSE_PATH, frames=frames)
    for table, count in counts.items():
        print(f"    {table}: {count} filas")

    print("[4/4] Quality: ejecutando controles de calidad...")
    conn = sqlite3.connect(DEFAULT_WAREHOUSE_PATH)
    try:
        checks = run_quality_checks(conn)
    finally:
        conn.close()

    print()
    print("Reporte de calidad del warehouse")
    print("-" * 60)
    for check in checks:
        mark = "✓" if check["ok"] else "✗"
        print(f"{mark} {check['nombre']}: {check['detalle']}")
    print("-" * 60)

    failed = [check for check in checks if not check["ok"]]
    if failed:
        print(f"Resultado: {len(failed)} de {len(checks)} controles fallaron.")
        return 1
    print(f"Resultado: {len(checks)}/{len(checks)} controles superados.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
