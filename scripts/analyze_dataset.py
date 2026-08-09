"""Exploratory data analysis over data/warehouse.db.

Generates the figures and the dimension-calibration CSV consumed by
docs/analisis/dataset-eda.md. Runs with the system python3 (pandas +
matplotlib from requirements-dev.txt); it does not touch the app runtime.

Usage:
    python3 scripts/analyze_dataset.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "warehouse.db"
FIG_DIR = ROOT / "docs" / "analisis" / "figuras"

LABEL_ORDER = ["verde", "amarillo", "rojo"]
LABEL_COLORS = {"verde": "#79A87E", "amarillo": "#DDAF54", "rojo": "#C26D62"}
LABEL_NAMES = {"verde": "Verde", "amarillo": "Amarillo", "rojo": "Rojo"}
NEUTRAL = "#7A93AC"
INK = "#333333"
GRID = "#E3E3E3"

NUMERIC_DIMS = ["dolor_nrs", "fiebre_c"]
CATEGORICAL_DIMS = ["movilidad", "herida", "apetito", "sueno"]


def configure_style() -> None:
    plt.rcParams.update(
        {
            "font.size": 10,
            "text.color": INK,
            "axes.edgecolor": "#BBBBBB",
            "axes.labelcolor": INK,
            "axes.titlesize": 12,
            "axes.titleweight": "bold",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "xtick.color": INK,
            "ytick.color": INK,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.dpi": 150,
            "savefig.bbox": "tight",
        }
    )


def load_frames(conn: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    return {
        "casos": pd.read_sql_query("SELECT * FROM casos", conn),
        "turnos": pd.read_sql_query("SELECT * FROM turnos", conn),
        "perfiles": pd.read_sql_query("SELECT * FROM perfiles_clinicos", conn),
    }


def save(fig: plt.Figure, name: str) -> None:
    path = FIG_DIR / name
    fig.savefig(path)
    plt.close(fig)
    print(f"figura escrita: {path.relative_to(ROOT)}")


def fig_label_distribution(casos: pd.DataFrame) -> None:
    counts = casos["label_ground_truth"].value_counts().reindex(LABEL_ORDER)
    fig, ax = plt.subplots(figsize=(6.5, 4))
    bars = ax.bar(
        [LABEL_NAMES[l] for l in LABEL_ORDER],
        counts.values,
        color=[LABEL_COLORS[l] for l in LABEL_ORDER],
        width=0.62,
        edgecolor="white",
        linewidth=1.5,
    )
    total = int(counts.sum())
    for bar, value in zip(bars, counts.values):
        ax.annotate(
            f"{value}\n({value / total:.1%})",
            (bar.get_x() + bar.get_width() / 2, value),
            ha="center",
            va="bottom",
            fontsize=10,
        )
    ax.set_ylim(0, counts.max() * 1.18)
    ax.set_ylabel("Casos")
    ax.set_title(f"Distribución de labels por caso (n={total})")
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    save(fig, "fig01_distribucion_labels.png")


def _stacked_barh(ax: plt.Axes, table: pd.DataFrame, min_label: int = 2) -> None:
    left = np.zeros(len(table))
    for label in LABEL_ORDER:
        values = table[label].values
        ax.barh(
            table.index,
            values,
            left=left,
            color=LABEL_COLORS[label],
            edgecolor="white",
            linewidth=1.5,
            label=LABEL_NAMES[label],
        )
        for i, value in enumerate(values):
            if value >= min_label:
                ax.text(
                    left[i] + value / 2,
                    i,
                    str(int(value)),
                    ha="center",
                    va="center",
                    fontsize=9,
                    color="#20301F" if label != "rojo" else "white",
                )
        left += values
    ax.invert_yaxis()
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def fig_labels_by_procedure(casos: pd.DataFrame) -> None:
    table = (
        casos.groupby(["procedimiento", "label_ground_truth"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=LABEL_ORDER, fill_value=0)
    )
    table = table.loc[table.sum(axis=1).sort_values(ascending=False).index]
    fig, ax = plt.subplots(figsize=(8, 4.2))
    _stacked_barh(ax, table)
    ax.set_xlabel("Casos")
    ax.set_title("Labels por procedimiento quirúrgico (32 casos por procedimiento)")
    ax.legend(loc="center left", bbox_to_anchor=(1.01, 0.5), frameon=False)
    save(fig, "fig02_labels_por_procedimiento.png")


def fig_labels_by_day(casos: pd.DataFrame) -> None:
    table = (
        casos.groupby(["dia_postop", "label_ground_truth"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=LABEL_ORDER, fill_value=0)
        .sort_index()
    )
    fig, ax = plt.subplots(figsize=(7, 4.2))
    x = np.arange(len(table))
    bottom = np.zeros(len(table))
    for label in LABEL_ORDER:
        values = table[label].values
        ax.bar(
            x,
            values,
            bottom=bottom,
            color=LABEL_COLORS[label],
            edgecolor="white",
            linewidth=1.5,
            width=0.6,
            label=LABEL_NAMES[label],
        )
        for i, value in enumerate(values):
            if value >= 2:
                ax.text(
                    x[i],
                    bottom[i] + value / 2,
                    str(int(value)),
                    ha="center",
                    va="center",
                    fontsize=9,
                    color="#20301F" if label != "rojo" else "white",
                )
        bottom += values
    ax.set_xticks(x, [f"Día {d}" for d in table.index])
    ax.set_ylabel("Casos")
    ax.set_title("Labels por día postoperatorio (40 casos por día)")
    ax.legend(loc="upper right", frameon=False, ncols=3)
    ax.set_ylim(0, 46)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    save(fig, "fig03_labels_por_dia_postop.png")


def _box_by_label(ax: plt.Axes, casos: pd.DataFrame, column: str) -> None:
    data = [casos.loc[casos["label_ground_truth"] == l, column].values for l in LABEL_ORDER]
    box = ax.boxplot(
        data,
        patch_artist=True,
        widths=0.5,
        medianprops={"color": INK, "linewidth": 1.6},
        whiskerprops={"color": "#888888"},
        capprops={"color": "#888888"},
        flierprops={
            "marker": "o",
            "markersize": 4,
            "markerfacecolor": "#888888",
            "markeredgecolor": "none",
        },
    )
    for patch, label in zip(box["boxes"], LABEL_ORDER):
        patch.set_facecolor(LABEL_COLORS[label])
        patch.set_alpha(0.55)
        patch.set_edgecolor("#777777")
    rng = np.random.default_rng(42)
    for i, (values, label) in enumerate(zip(data, LABEL_ORDER), start=1):
        jitter = rng.uniform(-0.12, 0.12, size=len(values))
        ax.scatter(
            np.full(len(values), i) + jitter,
            values,
            s=12,
            color=LABEL_COLORS[label],
            edgecolor="white",
            linewidth=0.4,
            zorder=3,
        )
    ax.set_xticks(
        range(1, len(LABEL_ORDER) + 1),
        [f"{LABEL_NAMES[l]}\n(n={len(d)})" for l, d in zip(LABEL_ORDER, data)],
    )
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def fig_pain_fever_by_label(casos: pd.DataFrame) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.5, 4.2))
    _box_by_label(ax1, casos, "dolor_nrs")
    ax1.set_title("Dolor (NRS 0-10) por label")
    ax1.set_ylabel("Dolor NRS")
    _box_by_label(ax2, casos, "fiebre_c")
    ax2.set_title("Temperatura (°C) por label")
    ax2.set_ylabel("Temperatura (°C)")
    ax2.axhline(38.0, color="#B0554B", linestyle="--", linewidth=1.2)
    ax2.annotate(
        "38.0 °C",
        (0.62, 38.03),
        fontsize=9,
        color="#B0554B",
    )
    fig.tight_layout()
    save(fig, "fig04_dolor_fiebre_por_label.png")


def fig_patient_styles(turnos: pd.DataFrame) -> None:
    styles = (
        turnos.groupby("caso_id")["estilo_paciente"].first().value_counts().sort_values()
    )
    fig, ax = plt.subplots(figsize=(7, 3.8))
    labels = [s.replace("_", " ") for s in styles.index]
    ax.barh(labels, styles.values, color=NEUTRAL, edgecolor="white", linewidth=1.5, height=0.62)
    for i, value in enumerate(styles.values):
        ax.text(value + 0.4, i, str(int(value)), va="center", fontsize=9)
    ax.set_xlabel("Casos")
    ax.set_title(f"Estilos de paciente (n={int(styles.sum())} casos)")
    ax.set_xlim(0, styles.max() * 1.12)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    save(fig, "fig05_estilos_paciente.png")


def fig_ages_by_procedure(perfiles: pd.DataFrame) -> None:
    order = (
        perfiles.groupby("procedimiento")["edad"].median().sort_values().index.tolist()
    )
    data = [perfiles.loc[perfiles["procedimiento"] == p, "edad"].values for p in order]
    fig, ax = plt.subplots(figsize=(8, 4.2))
    box = ax.boxplot(
        data,
        vert=False,
        patch_artist=True,
        widths=0.5,
        medianprops={"color": INK, "linewidth": 1.6},
        whiskerprops={"color": "#888888"},
        capprops={"color": "#888888"},
        flierprops={"marker": "o", "markersize": 4, "markerfacecolor": "#888888",
                    "markeredgecolor": "none"},
    )
    for patch in box["boxes"]:
        patch.set_facecolor(NEUTRAL)
        patch.set_alpha(0.45)
        patch.set_edgecolor("#777777")
    rng = np.random.default_rng(7)
    for i, values in enumerate(data, start=1):
        jitter = rng.uniform(-0.1, 0.1, size=len(values))
        ax.scatter(values, np.full(len(values), i) + jitter, s=14, color=NEUTRAL,
                   edgecolor="white", linewidth=0.4, zorder=3)
    ax.set_yticks(range(1, len(order) + 1), [f"{p}\n(n={len(d)})" for p, d in zip(order, data)],
                  fontsize=9)
    ax.set_xlabel("Edad (años)")
    ax.set_title("Edades por procedimiento quirúrgico")
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    save(fig, "fig06_edades_por_procedimiento.png")


def build_calibration_csv(casos: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for dim in NUMERIC_DIMS:
        for label in LABEL_ORDER:
            values = casos.loc[casos["label_ground_truth"] == label, dim]
            rows.append(
                {
                    "dimension": dim,
                    "tipo": "numerica",
                    "label": label,
                    "categoria": "",
                    "n_casos": int(values.size),
                    "mediana": round(float(values.median()), 2),
                    "p25": round(float(values.quantile(0.25)), 2),
                    "p75": round(float(values.quantile(0.75)), 2),
                    "minimo": round(float(values.min()), 2),
                    "maximo": round(float(values.max()), 2),
                    "pct_dentro_label": "",
                }
            )
    for dim in CATEGORICAL_DIMS:
        for label in LABEL_ORDER:
            subset = casos.loc[casos["label_ground_truth"] == label, dim]
            counts = subset.value_counts()
            for category, count in counts.items():
                rows.append(
                    {
                        "dimension": dim,
                        "tipo": "categorica",
                        "label": label,
                        "categoria": category,
                        "n_casos": int(count),
                        "mediana": "",
                        "p25": "",
                        "p75": "",
                        "minimo": "",
                        "maximo": "",
                        "pct_dentro_label": round(100 * count / subset.size, 1),
                    }
                )
    frame = pd.DataFrame(rows)
    path = FIG_DIR / "calibracion_dimensiones.csv"
    frame.to_csv(path, index=False)
    print(f"csv escrito: {path.relative_to(ROOT)} ({len(frame)} filas)")
    return frame


def print_verification(casos: pd.DataFrame, turnos: pd.DataFrame) -> None:
    print("\n--- verificación de cifras citadas en dataset-eda.md ---")
    print("labels:", casos["label_ground_truth"].value_counts().to_dict())
    print(
        "arquetipo x label:",
        casos.groupby(["arquetipo_trayectoria", "label_ground_truth"]).size().to_dict(),
    )
    for threshold, column in ((38.0, "fiebre_c"), (7, "dolor_nrs"), (5, "dolor_nrs")):
        subset = casos[casos[column] >= threshold]
        print(
            f"{column} >= {threshold}: {len(subset)} casos ->",
            subset["label_ground_truth"].value_counts().to_dict(),
        )
    herida_normal = casos[casos["herida"] == "normal"]
    print(
        f"herida normal: {len(herida_normal)} casos ->",
        herida_normal["label_ground_truth"].value_counts().to_dict(),
    )
    for dim, category in (
        ("herida", "secrecion_purulenta"),
        ("movilidad", "incapacitante_nueva"),
    ):
        subset = casos[casos[dim] == category]
        print(
            f"{dim}={category}: {len(subset)} casos ->",
            subset["label_ground_truth"].value_counts().to_dict(),
        )
    lengths = turnos.assign(longitud=turnos["texto"].str.len())
    print(
        "turnos por capa/hablante y longitud media:",
        lengths.groupby(["capa", "hablante"])["longitud"]
        .agg(["count", "mean"])
        .round(1)
        .to_dict("index"),
    )
    terceros = turnos[turnos["dialogo_id"].str.endswith("_c2_tercero")]
    print(
        f"turnos de tercero: {len(terceros)} en {terceros['caso_id'].nunique()} casos"
    )
    noisy = turnos[turnos["capa"] == "capa2_ruidosa"]
    print(
        "marcadores de ruido en capa2: inaudible=",
        int(noisy["texto"].str.contains(r"\[inaudible\]", regex=True).sum()),
        " silencio/vacio=",
        int(noisy["texto"].isin(["[silencio]", "..."]).sum()),
    )
    patient_text = turnos.loc[turnos["hablante"] == "paciente", "texto"].str.lower()
    regionalisms = {
        "pues": r"\bpues\b",
        "ahorita": r"\bahorita\b",
        "antier": r"\bantier\b",
        "mijo/mija": r"\bmij[oa]\b",
        "doctor(a)": r"\bdoctora?\b",
        "harto(a)": r"\bhart[oa]s?\b",
    }
    for term, pattern in regionalisms.items():
        count = int(patient_text.str.contains(pattern, regex=True).sum())
        print(f"regionalismo '{term}': {count} turnos de paciente")


def main() -> int:
    if not DB_PATH.exists():
        print(f"error: no existe {DB_PATH}; ejecute antes python3 -m scripts.run_etl", file=sys.stderr)
        return 1
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    configure_style()
    with sqlite3.connect(DB_PATH) as conn:
        frames = load_frames(conn)
    casos, turnos, perfiles = frames["casos"], frames["turnos"], frames["perfiles"]

    fig_label_distribution(casos)
    fig_labels_by_procedure(casos)
    fig_labels_by_day(casos)
    fig_pain_fever_by_label(casos)
    fig_patient_styles(turnos)
    fig_ages_by_procedure(perfiles)
    build_calibration_csv(casos)
    print_verification(casos, turnos)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
