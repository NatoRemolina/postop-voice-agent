"""Transformaciones: parse de celdas JSON, llaves derivadas y maestro de casos."""

import json

import pandas as pd

TERCERO_SUFFIX = "_c2_tercero"


def parse_json_list(value) -> list | None:
    """json.loads defensivo: devuelve la lista parseada o None si la celda no es una lista JSON valida."""
    if value is None:
        return None
    if not isinstance(value, str):
        if pd.isna(value):
            return None
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, list) else None


def transform_conversations(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["is_tercero"] = df["dialogo_id"].str.endswith(TERCERO_SUFFIX).fillna(False).astype("bool")
    return df


def transform_trajectories(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["caso_id"] = ("caso_" + df["trayectoria_id"]).astype("string")
    return df


def transform_clinical_profiles(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    parsed = df["comorbilidades"].map(parse_json_list)
    df["comorbilidades"] = parsed
    df["n_comorbilidades"] = parsed.map(lambda items: len(items) if isinstance(items, list) else None).astype("Int64")
    return df


def transform_demographics(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["adaptation_fields"] = df["adaptation_fields"].map(parse_json_list)
    return df


def build_cases_master(
    conversations: pd.DataFrame,
    trajectories: pd.DataFrame,
    clinical_profiles: pd.DataFrame,
    demographics: pd.DataFrame,
) -> pd.DataFrame:
    """Maestro caso -> paciente -> procedimiento -> demografia -> trayectoria -> label (1 fila por caso)."""
    labels = (
        conversations.groupby("caso_id", as_index=False)["label_ground_truth"]
        .first()
    )
    master = trajectories.merge(labels, on="caso_id", how="left")
    master = master.merge(
        clinical_profiles[
            [
                "paciente_id",
                "procedimiento",
                "fecha_cirugia",
                "edad",
                "genero",
                "comorbilidades",
                "n_comorbilidades",
                "complicacion_encounter",
            ]
        ],
        on="paciente_id",
        how="left",
    )
    master = master.merge(
        demographics[["paciente_id", "nombre_completo", "ciudad", "departamento", "eps"]],
        on="paciente_id",
        how="left",
    )
    columns = [
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
    ]
    return master[columns]
