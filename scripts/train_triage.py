"""Entrena y compara modelos de triaje clínico (verde/amarillo/rojo) desde el
warehouse ya construido por el ETL, y serializa el mejor a data/triage_model.pkl
como un pipeline completo (preprocesamiento + clasificador) para que
app/agent/triage_model.py pueda llamar model.predict_proba(dataframe_crudo)
directamente, sin duplicar lógica de encoding en el runtime.

Uso: python3 -m scripts.train_triage   (usa el python3 del sistema, con
pandas/scikit-learn — NO el .venv de la app, que se mantiene liviano).
"""

import pickle
import sqlite3
import warnings

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix, recall_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.tree import DecisionTreeClassifier, export_text

warnings.filterwarnings("ignore")

WAREHOUSE = "data/warehouse.db"
MODEL_OUT = "data/triage_model.pkl"
REPORT_OUT = "docs/analisis/modelo-triaje.md"

# Debe coincidir con detect_scenario() (app/agent/scenario.py): el nombre de
# escenario que el pipeline en producción realmente envía a predict(), no el
# nombre de procedimiento en español del warehouse — de lo contrario el
# feature "procedimiento" sería inútil en inferencia (categoría nunca vista).
PROCEDIMIENTO_A_ESCENARIO = {
    "Apendicectomía": "Appendicitis",
    "Colecistectomía": "cholecystitis",
    "Colectomía": "colorectal cancer",
    "Reemplazo de cadera/rodilla": "total joint replacement",
    "Mastectomía": "breast_cancer",
}

NUMERIC = ["dolor_nrs", "fiebre_c", "dia_postop", "edad", "n_comorbilidades"]
CATEGORICAL = ["movilidad", "herida", "apetito", "sueno", "procedimiento", "genero"]
FEATURES = NUMERIC + CATEGORICAL
CRITICALITY_ORDER = ["verde", "amarillo", "rojo"]


def load_data() -> tuple[pd.DataFrame, pd.Series]:
    conn = sqlite3.connect(WAREHOUSE)
    df = pd.read_sql(
        "SELECT dolor_nrs, fiebre_c, dia_postop, edad, n_comorbilidades, "
        "movilidad, herida, apetito, sueno, procedimiento, genero, "
        "label_ground_truth, arquetipo_trayectoria FROM casos",
        conn,
    )
    conn.close()
    # Fuga de información verificada en el EDA: arquetipo_trayectoria casi
    # determina el label (los 12 rojos están en "complicacion_real"). Se lee
    # solo para el chequeo de abajo, nunca entra como feature del modelo.
    df["procedimiento"] = df["procedimiento"].map(PROCEDIMIENTO_A_ESCENARIO)
    assert df["procedimiento"].notna().all(), "procedimiento sin mapear a escenario"
    return df, df["label_ground_truth"]


def build_pipeline(estimator) -> Pipeline:
    pre = ColumnTransformer(
        transformers=[
            ("num", StandardScaler(), NUMERIC),
            ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL),
        ]
    )
    return Pipeline([("pre", pre), ("clf", estimator)])


def rojo_recall(y_true, y_pred) -> float:
    return recall_score(y_true, y_pred, labels=["rojo"], average="macro", zero_division=0)


def evaluate(name: str, pipe: Pipeline, X: pd.DataFrame, y: pd.Series, cv) -> dict:
    y_pred = cross_val_predict(pipe, X, y, cv=cv)
    report = classification_report(y, y_pred, labels=CRITICALITY_ORDER, zero_division=0)
    cm = confusion_matrix(y, y_pred, labels=CRITICALITY_ORDER)
    r_rojo = rojo_recall(y, y_pred)
    r_amarillo = recall_score(y, y_pred, labels=["amarillo"], average="macro", zero_division=0)
    print(f"\n=== {name} ===")
    print(report)
    print("matriz de confusión (filas=real, cols=predicho), orden verde/amarillo/rojo:")
    print(cm)
    print(f"recall rojo: {r_rojo:.3f} | recall amarillo: {r_amarillo:.3f}")
    return {
        "name": name, "pipe": pipe, "report": report, "cm": cm,
        "recall_rojo": r_rojo, "recall_amarillo": r_amarillo,
    }


def main() -> int:
    df, y = load_data()
    X = df[FEATURES]
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)

    candidates = [
        evaluate("Regresión logística (línea base)",
                  build_pipeline(LogisticRegression(max_iter=1000, class_weight="balanced")),
                  X, y, cv),
        evaluate("Árbol de decisión (profundidad 4, interpretable)",
                  build_pipeline(DecisionTreeClassifier(max_depth=4, class_weight="balanced", random_state=42)),
                  X, y, cv),
        evaluate("Random Forest (balanceado)",
                  build_pipeline(RandomForestClassifier(
                      n_estimators=300, max_depth=6, class_weight="balanced", random_state=42)),
                  X, y, cv),
    ]

    best = max(candidates, key=lambda c: (c["recall_rojo"], c["recall_amarillo"]))
    print(f"\n>>> Mejor modelo por recall de rojo: {best['name']}")

    # Fit final sobre TODOS los datos (la elección ya se validó por CV arriba).
    final_pipe = best["pipe"]
    final_pipe.fit(X, y)

    with open(MODEL_OUT, "wb") as f:
        pickle.dump(final_pipe, f)
    print(f"Modelo serializado en {MODEL_OUT}")

    tree_text = None
    tree_candidate = next((c for c in candidates if "Árbol" in c["name"]), None)
    if tree_candidate is not None:
        tree_pipe = tree_candidate["pipe"]
        tree_pipe.fit(X, y)
        feature_names = (
            NUMERIC
            + list(tree_pipe.named_steps["pre"].named_transformers_["cat"].get_feature_names_out(CATEGORICAL))
        )
        tree_text = export_text(tree_pipe.named_steps["clf"], feature_names=feature_names, max_depth=4)

    rf_candidate = next((c for c in candidates if "Random Forest" in c["name"]), None)
    importances_text = None
    if rf_candidate is not None:
        rf_pipe = rf_candidate["pipe"]
        rf_pipe.fit(X, y)
        feature_names = (
            NUMERIC
            + list(rf_pipe.named_steps["pre"].named_transformers_["cat"].get_feature_names_out(CATEGORICAL))
        )
        importances = rf_pipe.named_steps["clf"].feature_importances_
        order = np.argsort(importances)[::-1]
        importances_text = "\n".join(
            f"- {feature_names[i]}: {importances[i]:.3f}" for i in order[:15]
        )

    write_report(df, candidates, best, tree_text, importances_text)
    return 0


def write_report(df, candidates, best, tree_text, importances_text) -> None:
    lines = []
    lines.append("# Modelo de triaje clínico — model card\n")
    lines.append(
        "Modelo de ML clásico entrenado sobre las 6 dimensiones clínicas del "
        "checklist postoperatorio, para dar una segunda opinión determinista al "
        "juicio del LLM en `registrar_evaluacion` (`app/graph/tools.py`) — el "
        "ensemble aplica `max(criticidad_llm, criticidad_ml)`: el modelo solo "
        "puede subir la criticidad, nunca bajarla.\n"
    )
    lines.append("## Datos y features\n")
    lines.append(
        f"- {len(df)} casos, {df['label_ground_truth'].value_counts().to_dict()} "
        "(desbalanceado, como advierte el reto).\n"
        "- Features: dolor_nrs, fiebre_c, dia_postop, edad, n_comorbilidades "
        "(numéricas) + movilidad, herida, apetito, sueno, procedimiento, genero "
        "(categóricas, one-hot).\n"
        "- **`procedimiento` se re-mapea de español (warehouse) a los mismos "
        "nombres de escenario que produce `detect_scenario()` en producción** "
        "— si no, el modelo entrenaría con categorías que la inferencia real "
        "nunca reproduce.\n"
        "- **`arquetipo_trayectoria` EXCLUIDO deliberadamente**: en el EDA se "
        "verificó que casi determina el label (los 12 casos rojos están "
        "concentrados en el arquetipo `complicacion_real`) — usarlo sería fuga "
        "de información, ya que en una llamada real el agente nunca conoce el "
        "arquetipo, solo los síntomas reportados.\n"
    )
    lines.append("## Comparación de modelos (validación cruzada estratificada, 5 folds)\n")
    lines.append("| Modelo | Recall rojo | Recall amarillo |\n|---|---|---|\n")
    for c in candidates:
        marca = " **(elegido)**" if c is best else ""
        lines.append(f"| {c['name']}{marca} | {c['recall_rojo']:.3f} | {c['recall_amarillo']:.3f} |\n")
    lines.append(f"\n**Modelo elegido: {best['name']}**, por maximizar el recall de la clase "
                  "rojo — la rúbrica del reto trata el falso negativo (no escalar cuando "
                  "tocaba) como la falla catastrófica, así que es la métrica que manda, "
                  "antes que accuracy global.\n")
    lines.append(f"\n```\n{best['report']}\n```\n")
    lines.append(f"\nMatriz de confusión (filas=real, columnas=predicho, orden "
                  f"verde/amarillo/rojo):\n\n```\n{best['cm']}\n```\n")
    if tree_text:
        lines.append("## Árbol de decisión (referencia interpretable, profundidad 4)\n")
        lines.append(f"```\n{tree_text}\n```\n")
    if importances_text:
        lines.append("## Importancia de variables (Random Forest)\n")
        lines.append(f"{importances_text}\n")
    lines.append("## Limitaciones\n")
    lines.append(
        "- Dataset sintético, n=160, solo 12 casos rojo — el modelo es un "
        "complemento del juicio del LLM, no un reemplazo; por diseño solo "
        "puede subir la criticidad, nunca bajarla.\n"
        "- No valida clínicamente: es una señal estadística sobre datos "
        "sintéticos del reto, no un dispositivo médico.\n"
    )
    with open(REPORT_OUT, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"Model card escrita en {REPORT_OUT}")


if __name__ == "__main__":
    raise SystemExit(main())
