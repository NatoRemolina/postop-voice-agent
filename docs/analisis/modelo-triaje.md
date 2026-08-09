# Modelo de triaje clínico — model card
Modelo de ML clásico entrenado sobre las 6 dimensiones clínicas del checklist postoperatorio, para dar una segunda opinión determinista al juicio del LLM en `registrar_evaluacion` (`app/graph/tools.py`) — el ensemble aplica `max(criticidad_llm, criticidad_ml)`: el modelo solo puede subir la criticidad, nunca bajarla.
## Datos y features
- 160 casos, {'verde': 123, 'amarillo': 25, 'rojo': 12} (desbalanceado, como advierte el reto).
- Features: dolor_nrs, fiebre_c, dia_postop, edad, n_comorbilidades (numéricas) + movilidad, herida, apetito, sueno, procedimiento, genero (categóricas, one-hot).
- **`procedimiento` se re-mapea de español (warehouse) a los mismos nombres de escenario que produce `detect_scenario()` en producción** — si no, el modelo entrenaría con categorías que la inferencia real nunca reproduce.
- **`arquetipo_trayectoria` EXCLUIDO deliberadamente**: en el EDA se verificó que casi determina el label (los 12 casos rojos están concentrados en el arquetipo `complicacion_real`) — usarlo sería fuga de información, ya que en una llamada real el agente nunca conoce el arquetipo, solo los síntomas reportados.
## Comparación de modelos (validación cruzada estratificada, 5 folds)
| Modelo | Recall rojo | Recall amarillo |
|---|---|---|
| Regresión logística (línea base) | 1.000 | 0.920 |
| Árbol de decisión (profundidad 4, interpretable) | 0.917 | 0.800 |
| Random Forest (balanceado) **(elegido)** | 1.000 | 1.000 |

**Modelo elegido: Random Forest (balanceado)**, por maximizar el recall de la clase rojo — la rúbrica del reto trata el falso negativo (no escalar cuando tocaba) como la falla catastrófica, así que es la métrica que manda, antes que accuracy global.

```
              precision    recall  f1-score   support

       verde       1.00      0.96      0.98       123
    amarillo       0.83      1.00      0.91        25
        rojo       1.00      1.00      1.00        12

    accuracy                           0.97       160
   macro avg       0.94      0.99      0.96       160
weighted avg       0.97      0.97      0.97       160

```

Matriz de confusión (filas=real, columnas=predicho, orden verde/amarillo/rojo):

```
[[118   5   0]
 [  0  25   0]
 [  0   0  12]]
```
## Árbol de decisión (referencia interpretable, profundidad 4)
```
|--- fiebre_c <= 1.33
|   |--- herida_normal <= 0.50
|   |   |--- dolor_nrs <= -0.77
|   |   |   |--- class: verde
|   |   |--- dolor_nrs >  -0.77
|   |   |   |--- apetito_normal <= 0.50
|   |   |   |   |--- class: amarillo
|   |   |   |--- apetito_normal >  0.50
|   |   |   |   |--- class: amarillo
|   |--- herida_normal >  0.50
|   |   |--- sueno_muy_alterado <= 0.50
|   |   |   |--- apetito_muy_disminuido <= 0.50
|   |   |   |   |--- class: verde
|   |   |   |--- apetito_muy_disminuido >  0.50
|   |   |   |   |--- class: verde
|   |   |--- sueno_muy_alterado >  0.50
|   |   |   |--- movilidad_limitada_esperada <= 0.50
|   |   |   |   |--- class: amarillo
|   |   |   |--- movilidad_limitada_esperada >  0.50
|   |   |   |   |--- class: verde
|--- fiebre_c >  1.33
|   |--- apetito_levemente_disminuido <= 0.50
|   |   |--- class: rojo
|   |--- apetito_levemente_disminuido >  0.50
|   |   |--- edad <= 0.25
|   |   |   |--- class: verde
|   |   |--- edad >  0.25
|   |   |   |--- class: amarillo

```
## Importancia de variables (Random Forest)
- fiebre_c: 0.230
- dolor_nrs: 0.123
- apetito_muy_disminuido: 0.096
- sueno_muy_alterado: 0.093
- herida_normal: 0.092
- dia_postop: 0.062
- herida_eritema_leve: 0.053
- apetito_normal: 0.047
- sueno_normal: 0.040
- edad: 0.023
- apetito_levemente_disminuido: 0.021
- genero_M: 0.018
- genero_F: 0.018
- n_comorbilidades: 0.017
- sueno_levemente_alterado: 0.013
## Limitaciones
- Dataset sintético, n=160, solo 12 casos rojo — el modelo es un complemento del juicio del LLM, no un reemplazo; por diseño solo puede subir la criticidad, nunca bajarla.
- No valida clínicamente: es una señal estadística sobre datos sintéticos del reto, no un dispositivo médico.
- **Sensible a combinaciones de síntomas fuera de distribución.** Probado con
  fiebre 38.5°C + herida con secreción purulenta pero movilidad/apetito/sueño
  en "normal" (una combinación que nunca ocurre en los 160 casos reales — ahí
  esas tres señales también están alteradas en los 12 rojos), el modelo
  predijo "amarillo" en vez de "rojo". No es un escenario realista de un turno
  de llamada real (`registrar_evaluacion` pasa todos los síntomas relevados,
  no uno aislado), pero es la razón concreta por la que el ensemble nunca
  reemplaza el juicio del LLM: el prompt del agente ya instruye escalar ante
  secreción purulenta como señal de alarma explícita, independientemente de
  lo que diga el modelo.
