"""Controles de calidad sobre el warehouse ya cargado."""

import json
import sqlite3

EXPECTED_CASES = 160
EXPECTED_PATIENTS = 40
EXPECTED_TURNS = 3991
EXPECTED_LABEL_DISTRIBUTION = {"verde": 123, "amarillo": 25, "rojo": 12}


def _scalar(conn: sqlite3.Connection, sql: str) -> int:
    return conn.execute(sql).fetchone()[0]


def _check_case_counts(conn: sqlite3.Connection) -> dict:
    counts = {
        "casos": _scalar(conn, "SELECT COUNT(*) FROM casos"),
        "trayectorias": _scalar(conn, "SELECT COUNT(DISTINCT caso_id) FROM trayectorias"),
        "turnos": _scalar(conn, "SELECT COUNT(DISTINCT caso_id) FROM turnos"),
    }
    ok = all(value == EXPECTED_CASES for value in counts.values())
    detail = ", ".join(f"{table}={value}" for table, value in counts.items())
    return {
        "nombre": "casos_160_en_cada_dimension",
        "ok": ok,
        "detalle": f"esperado {EXPECTED_CASES} por dimension; observado: {detail}",
    }


def _check_patient_counts(conn: sqlite3.Connection) -> dict:
    counts = {
        "pacientes": _scalar(conn, "SELECT COUNT(*) FROM pacientes"),
        "perfiles_clinicos": _scalar(conn, "SELECT COUNT(*) FROM perfiles_clinicos"),
        "casos": _scalar(conn, "SELECT COUNT(DISTINCT paciente_id) FROM casos"),
        "turnos": _scalar(conn, "SELECT COUNT(DISTINCT paciente_id) FROM turnos"),
    }
    ok = all(value == EXPECTED_PATIENTS for value in counts.values())
    detail = ", ".join(f"{table}={value}" for table, value in counts.items())
    return {
        "nombre": "pacientes_40_en_cada_dimension",
        "ok": ok,
        "detalle": f"esperado {EXPECTED_PATIENTS} por dimension; observado: {detail}",
    }


def _check_label_constant_per_case(conn: sqlite3.Connection) -> dict:
    violations = _scalar(
        conn,
        """
        SELECT COUNT(*) FROM (
            SELECT caso_id FROM turnos
            GROUP BY caso_id
            HAVING COUNT(DISTINCT label_ground_truth) > 1
        )
        """,
    )
    mismatches = _scalar(
        conn,
        """
        SELECT COUNT(*) FROM casos c
        JOIN (SELECT caso_id, MAX(label_ground_truth) AS label FROM turnos GROUP BY caso_id) t
          ON t.caso_id = c.caso_id
        WHERE t.label <> c.label_ground_truth
        """,
    )
    ok = violations == 0 and mismatches == 0
    return {
        "nombre": "label_constante_por_caso",
        "ok": ok,
        "detalle": (
            f"casos con mas de un label en turnos: {violations}; "
            f"discrepancias turnos vs maestro: {mismatches}"
        ),
    }


def _check_label_distribution(conn: sqlite3.Connection) -> dict:
    observed = dict(
        conn.execute(
            "SELECT label_ground_truth, COUNT(*) FROM casos GROUP BY label_ground_truth"
        ).fetchall()
    )
    ok = observed == EXPECTED_LABEL_DISTRIBUTION
    return {
        "nombre": "distribucion_labels_123_25_12",
        "ok": ok,
        "detalle": f"esperado {EXPECTED_LABEL_DISTRIBUTION}; observado {observed}",
    }


def _check_case_trajectory_join(conn: sqlite3.Connection) -> dict:
    orphan_cases = _scalar(
        conn,
        """
        SELECT COUNT(*) FROM casos c
        LEFT JOIN trayectorias t ON t.caso_id = c.caso_id
        WHERE t.caso_id IS NULL
        """,
    )
    orphan_trajectories = _scalar(
        conn,
        """
        SELECT COUNT(*) FROM trayectorias t
        LEFT JOIN casos c ON c.caso_id = t.caso_id
        WHERE c.caso_id IS NULL
        """,
    )
    ok = orphan_cases == 0 and orphan_trajectories == 0
    return {
        "nombre": "join_casos_trayectorias_sin_huerfanos",
        "ok": ok,
        "detalle": (
            f"casos sin trayectoria: {orphan_cases}; "
            f"trayectorias sin caso: {orphan_trajectories}"
        ),
    }


def _count_invalid_json_lists(conn: sqlite3.Connection, table: str, column: str) -> int:
    invalid = 0
    for (value,) in conn.execute(f"SELECT {column} FROM {table}"):
        if value is None:
            invalid += 1
            continue
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            invalid += 1
            continue
        if not isinstance(parsed, list):
            invalid += 1
    return invalid


def _check_json_columns(conn: sqlite3.Connection) -> dict:
    invalid_comorbidities = _count_invalid_json_lists(conn, "perfiles_clinicos", "comorbilidades")
    invalid_adaptation = _count_invalid_json_lists(conn, "pacientes", "adaptation_fields")
    ok = invalid_comorbidities == 0 and invalid_adaptation == 0
    return {
        "nombre": "json_parseado_sin_error",
        "ok": ok,
        "detalle": (
            f"comorbilidades invalidas: {invalid_comorbidities}; "
            f"adaptation_fields invalidos: {invalid_adaptation}"
        ),
    }


def _check_turns_by_layer(conn: sqlite3.Connection) -> dict:
    by_layer = dict(
        conn.execute("SELECT capa, COUNT(*) FROM turnos GROUP BY capa").fetchall()
    )
    total = sum(by_layer.values())
    ok = total == EXPECTED_TURNS
    detail = ", ".join(f"{layer}={count}" for layer, count in sorted(by_layer.items()))
    return {
        "nombre": "turnos_por_capa_suman_3991",
        "ok": ok,
        "detalle": f"{detail}; total={total} (esperado {EXPECTED_TURNS})",
    }


def run_quality_checks(conn: sqlite3.Connection) -> list[dict]:
    return [
        _check_case_counts(conn),
        _check_patient_counts(conn),
        _check_label_constant_per_case(conn),
        _check_label_distribution(conn),
        _check_case_trajectory_join(conn),
        _check_json_columns(conn),
        _check_turns_by_layer(conn),
    ]
