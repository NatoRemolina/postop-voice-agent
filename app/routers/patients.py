import sqlite3

from fastapi import APIRouter, HTTPException

from app.config import BASE_DIR

WAREHOUSE_PATH = BASE_DIR / "data" / "warehouse.db"

router = APIRouter()


@router.get("/api/patients")
async def list_patients():
    if not WAREHOUSE_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail=(
                "El warehouse (data/warehouse.db) no existe. "
                "Genéralo con: python3 -m scripts.run_etl"
            ),
        )
    conn = sqlite3.connect(WAREHOUSE_PATH)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT caso_id, paciente_id, nombre_completo, ciudad,
                   procedimiento, dia_postop, edad, genero
            FROM casos
            ORDER BY paciente_id, dia_postop, caso_id
            """
        ).fetchall()
    finally:
        conn.close()
    # Minimización de datos: solo el nombre de pila, nunca el nombre completo.
    return [
        {
            "paciente_id": row["paciente_id"],
            "nombre": row["nombre_completo"].split()[0] if row["nombre_completo"] else "",
            "ciudad": row["ciudad"],
            "procedimiento": row["procedimiento"],
            "dia_postop": row["dia_postop"],
            "caso_id": row["caso_id"],
            "edad": row["edad"],
            "genero": row["genero"],
        }
        for row in rows
    ]
