"""Lectura tipada de los archivos fuente del dataset (hoja "result" de cada xlsx)."""

import warnings
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
DATASET_DIR = BASE_DIR / "dataset"
SHEET_NAME = "result"

CONVERSATIONS_XLSX = DATASET_DIR / "dataset_final.xlsx"
TRAJECTORIES_XLSX = DATASET_DIR / "trayectorias_postop_silver.xlsx"
CLINICAL_PROFILES_XLSX = DATASET_DIR / "perfiles_clinicos_pacientes_silver_contest.xlsx"
DEMOGRAPHICS_XLSX = DATASET_DIR / "perfiles_pacientes_co.xlsx"


def _read_sheet(
    path: Path,
    string_columns: tuple[str, ...] = (),
    int_columns: tuple[str, ...] = (),
    float_columns: tuple[str, ...] = (),
    bool_columns: tuple[str, ...] = (),
    datetime_columns: tuple[str, ...] = (),
) -> pd.DataFrame:
    with warnings.catch_warnings():
        # Los xlsx del reto no traen estilo por defecto; openpyxl lo advierte sin efecto.
        warnings.filterwarnings("ignore", message="Workbook contains no default style")
        df = pd.read_excel(path, sheet_name=SHEET_NAME)
    for column in string_columns:
        df[column] = df[column].astype("string")
    for column in int_columns:
        df[column] = df[column].astype("int64")
    for column in float_columns:
        df[column] = df[column].astype("float64")
    for column in bool_columns:
        df[column] = df[column].astype("bool")
    for column in datetime_columns:
        df[column] = pd.to_datetime(df[column])
    return df


def read_conversations() -> pd.DataFrame:
    return _read_sheet(
        CONVERSATIONS_XLSX,
        string_columns=(
            "dialogo_id",
            "caso_id",
            "paciente_id",
            "hablante",
            "texto",
            "label_ground_truth",
            "estilo_paciente",
            "modelo_paciente",
            "modelo_agente",
            "capa",
        ),
        int_columns=("dia_postop", "turno_idx"),
        datetime_columns=("generado_ts",),
    )


def read_trajectories() -> pd.DataFrame:
    return _read_sheet(
        TRAJECTORIES_XLSX,
        string_columns=(
            "trayectoria_id",
            "paciente_id",
            "arquetipo_trayectoria",
            "movilidad",
            "herida",
            "apetito",
            "sueno",
        ),
        int_columns=("dia_postop", "dolor_nrs", "seed"),
        float_columns=("fiebre_c",),
        datetime_columns=("generado_ts",),
    )


def read_clinical_profiles() -> pd.DataFrame:
    return _read_sheet(
        CLINICAL_PROFILES_XLSX,
        string_columns=(
            "paciente_id",
            "bundle_id",
            "synthea_runtime",
            "modulo_synthea",
            "procedimiento",
            "genero",
            "comorbilidades",
        ),
        int_columns=("edad",),
        bool_columns=("complicacion_encounter",),
        datetime_columns=("fecha_cirugia", "generado_ts"),
    )


def read_demographics() -> pd.DataFrame:
    return _read_sheet(
        DEMOGRAPHICS_XLSX,
        string_columns=(
            "paciente_id",
            "nombre_completo",
            "direccion",
            "ciudad",
            "departamento",
            "eps",
            "source_country",
            "adapted_country",
            "adaptation_fields",
        ),
        int_columns=("documento_cc",),
        datetime_columns=("adaptation_ts",),
    )
