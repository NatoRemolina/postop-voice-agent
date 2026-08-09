"""Carga del warehouse SQLite (idempotente: DROP TABLE IF EXISTS + recarga completa)."""

import json
import sqlite3
from pathlib import Path

import pandas as pd

from etl.extract import (
    BASE_DIR,
    read_clinical_profiles,
    read_conversations,
    read_demographics,
    read_trajectories,
)
from etl.transform import (
    build_cases_master,
    transform_clinical_profiles,
    transform_conversations,
    transform_demographics,
    transform_trajectories,
)

DEFAULT_WAREHOUSE_PATH = BASE_DIR / "data" / "warehouse.db"

_DDL = {
    "pacientes": """
        CREATE TABLE pacientes (
            paciente_id TEXT PRIMARY KEY,
            nombre_completo TEXT NOT NULL,
            direccion TEXT,
            ciudad TEXT,
            departamento TEXT,
            documento_cc INTEGER,
            eps TEXT,
            source_country TEXT,
            adapted_country TEXT,
            adaptation_fields TEXT,
            adaptation_ts TEXT
        )
    """,
    "perfiles_clinicos": """
        CREATE TABLE perfiles_clinicos (
            paciente_id TEXT PRIMARY KEY,
            bundle_id TEXT,
            synthea_runtime TEXT,
            modulo_synthea TEXT,
            procedimiento TEXT,
            fecha_cirugia TEXT,
            edad INTEGER,
            genero TEXT,
            comorbilidades TEXT,
            n_comorbilidades INTEGER,
            complicacion_encounter INTEGER,
            generado_ts TEXT
        )
    """,
    "trayectorias": """
        CREATE TABLE trayectorias (
            trayectoria_id TEXT PRIMARY KEY,
            caso_id TEXT NOT NULL,
            paciente_id TEXT NOT NULL,
            dia_postop INTEGER,
            arquetipo_trayectoria TEXT,
            dolor_nrs INTEGER,
            fiebre_c REAL,
            movilidad TEXT,
            herida TEXT,
            apetito TEXT,
            sueno TEXT,
            seed INTEGER,
            generado_ts TEXT
        )
    """,
    "casos": """
        CREATE TABLE casos (
            caso_id TEXT PRIMARY KEY,
            trayectoria_id TEXT NOT NULL,
            paciente_id TEXT NOT NULL,
            dia_postop INTEGER,
            label_ground_truth TEXT,
            arquetipo_trayectoria TEXT,
            dolor_nrs INTEGER,
            fiebre_c REAL,
            movilidad TEXT,
            herida TEXT,
            apetito TEXT,
            sueno TEXT,
            procedimiento TEXT,
            fecha_cirugia TEXT,
            edad INTEGER,
            genero TEXT,
            comorbilidades TEXT,
            n_comorbilidades INTEGER,
            complicacion_encounter INTEGER,
            nombre_completo TEXT,
            ciudad TEXT,
            departamento TEXT,
            eps TEXT
        )
    """,
    "turnos": """
        CREATE TABLE turnos (
            dialogo_id TEXT NOT NULL,
            caso_id TEXT NOT NULL,
            paciente_id TEXT NOT NULL,
            dia_postop INTEGER,
            turno_idx INTEGER,
            hablante TEXT,
            texto TEXT,
            label_ground_truth TEXT,
            estilo_paciente TEXT,
            modelo_paciente TEXT,
            modelo_agente TEXT,
            capa TEXT,
            is_tercero INTEGER,
            generado_ts TEXT
        )
    """,
}

_INDEXES = (
    "CREATE INDEX idx_turnos_caso_id ON turnos (caso_id)",
    "CREATE INDEX idx_turnos_paciente_id ON turnos (paciente_id)",
    "CREATE INDEX idx_trayectorias_caso_id ON trayectorias (caso_id)",
    "CREATE INDEX idx_trayectorias_paciente_id ON trayectorias (paciente_id)",
    "CREATE INDEX idx_casos_paciente_id ON casos (paciente_id)",
)

_TABLE_COLUMNS = {
    "pacientes": (
        "paciente_id",
        "nombre_completo",
        "direccion",
        "ciudad",
        "departamento",
        "documento_cc",
        "eps",
        "source_country",
        "adapted_country",
        "adaptation_fields",
        "adaptation_ts",
    ),
    "perfiles_clinicos": (
        "paciente_id",
        "bundle_id",
        "synthea_runtime",
        "modulo_synthea",
        "procedimiento",
        "fecha_cirugia",
        "edad",
        "genero",
        "comorbilidades",
        "n_comorbilidades",
        "complicacion_encounter",
        "generado_ts",
    ),
    "trayectorias": (
        "trayectoria_id",
        "caso_id",
        "paciente_id",
        "dia_postop",
        "arquetipo_trayectoria",
        "dolor_nrs",
        "fiebre_c",
        "movilidad",
        "herida",
        "apetito",
        "sueno",
        "seed",
        "generado_ts",
    ),
    "casos": (
        "caso_id",
        "trayectoria_id",
        "paciente_id",
        "dia_postop",
        "label_ground_truth",
        "arquetipo_trayectoria",
        "dolor_nrs",
        "fiebre_c",
        "movilidad",
        "herida",
        "apetito",
        "sueno",
        "procedimiento",
        "fecha_cirugia",
        "edad",
        "genero",
        "comorbilidades",
        "n_comorbilidades",
        "complicacion_encounter",
        "nombre_completo",
        "ciudad",
        "departamento",
        "eps",
    ),
    "turnos": (
        "dialogo_id",
        "caso_id",
        "paciente_id",
        "dia_postop",
        "turno_idx",
        "hablante",
        "texto",
        "label_ground_truth",
        "estilo_paciente",
        "modelo_paciente",
        "modelo_agente",
        "capa",
        "is_tercero",
        "generado_ts",
    ),
}


def _to_sql_value(value):
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat(sep=" ")
    if isinstance(value, bool):
        return int(value)
    if hasattr(value, "item"):
        return value.item()
    return value


def _to_records(df: pd.DataFrame, columns: tuple[str, ...]) -> list[tuple]:
    return [
        tuple(_to_sql_value(value) for value in row)
        for row in df[list(columns)].itertuples(index=False, name=None)
    ]


def build_frames() -> dict[str, pd.DataFrame]:
    """Ejecuta extract + transform y devuelve los dataframes listos para cargar."""
    conversations = transform_conversations(read_conversations())
    trajectories = transform_trajectories(read_trajectories())
    clinical_profiles = transform_clinical_profiles(read_clinical_profiles())
    demographics = transform_demographics(read_demographics())
    cases = build_cases_master(conversations, trajectories, clinical_profiles, demographics)
    return {
        "pacientes": demographics,
        "perfiles_clinicos": clinical_profiles,
        "trayectorias": trajectories,
        "casos": cases,
        "turnos": conversations,
    }


def load_warehouse(
    path: Path | str = DEFAULT_WAREHOUSE_PATH,
    frames: dict[str, pd.DataFrame] | None = None,
) -> dict[str, int]:
    """Escribe el warehouse SQLite y devuelve el conteo de filas por tabla."""
    if frames is None:
        frames = build_frames()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    conn = sqlite3.connect(path)
    try:
        with conn:
            for table, ddl in _DDL.items():
                conn.execute(f"DROP TABLE IF EXISTS {table}")
                conn.execute(ddl)
                columns = _TABLE_COLUMNS[table]
                placeholders = ", ".join("?" for _ in columns)
                records = _to_records(frames[table], columns)
                conn.executemany(
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                    records,
                )
                counts[table] = len(records)
            for index_ddl in _INDEXES:
                conn.execute(index_ddl)
    finally:
        conn.close()
    return counts
