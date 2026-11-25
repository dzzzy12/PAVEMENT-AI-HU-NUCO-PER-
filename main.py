from __future__ import annotations

import io
import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from flask import Flask, abort, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from ml.data_loader import PATOLOGIAS, SEVERIDADES, load_inspections
from ml.regression_model import PavementRegressionModel


ROOT_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = ROOT_DIR / "static" / "uploads"
REGISTRY_PATH = ROOT_DIR / "data" / "patologias_reg.json"
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
IMAGE_SIGNATURES = {
    "image/jpeg": lambda content: content.startswith(b"\xff\xd8\xff"),
    "image/png": lambda content: content.startswith(b"\x89PNG\r\n\x1a\n"),
    "image/webp": lambda content: content.startswith(b"RIFF") and content[8:12] == b"WEBP",
}

app = Flask(__name__, template_folder=str(ROOT_DIR / "templates"), static_folder=str(ROOT_DIR / "static"))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES + 128 * 1024

inspections, data_quality = load_inspections()
regression = PavementRegressionModel(inspections)
known_segment_ids = set(inspections["tramo_id"].astype(str))


@app.errorhandler(HTTPException)
def handle_http_error(error: HTTPException):
    original = getattr(error, "original_exception", None)
    if original is not None:
        app.logger.error(
            "Unhandled request exception",
            exc_info=(type(original), original, original.__traceback__),
        )
    return jsonify(detail=error.description), error.code


@app.errorhandler(RequestEntityTooLarge)
def handle_request_too_large(_error: RequestEntityTooLarge):
    return jsonify(detail="La imagen supera el límite de 8 MB."), 413


def _native(value):
    if isinstance(value, dict):
        return {key: _native(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_native(item) for item in value]
    if isinstance(value, tuple):
        return [_native(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    if pd.isna(value):
        return None
    return value


def _registry() -> list[dict]:
    if not REGISTRY_PATH.exists():
        return []
    try:
        records = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        abort(500, description="No se pudo leer el registro de patologías.")
    if not isinstance(records, list):
        abort(500, description="El registro de patologías tiene un formato inválido.")
    return records


def _prediction_payload() -> tuple[float, float]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(400, description="Envía antiguedad_actual y horizonte_anios como JSON.")

    values = []
    for field, minimum, maximum in (
        ("antiguedad_actual", 0, 100),
        ("horizonte_anios", 0, 50),
    ):
        value = payload.get(field)
        if isinstance(value, bool):
            abort(422, description=f"{field} debe ser un número entre {minimum} y {maximum}.")
        try:
            number = float(value)
        except (TypeError, ValueError):
            abort(422, description=f"{field} debe ser un número entre {minimum} y {maximum}.")
        if not math.isfinite(number) or not minimum <= number <= maximum:
            abort(422, description=f"{field} debe ser un número entre {minimum} y {maximum}.")
        values.append(number)
    return values[0], values[1]


def _prediction_result(current_age: float, horizon: float) -> dict:
    future_age = current_age + horizon
    current = regression.predict(current_age)
    future = regression.predict(future_age)
    return {
        "actual": current,
        "futuro": future,
        "antiguedad_actual": current_age,
        "horizonte_anios": horizon,
        "antiguedad_futura": future_age,
        "extrapolacion": bool(current["extrapolacion"] or future["extrapolacion"]),
        "rango_historico": {"min": regression.min_age, "max": regression.max_age},
        "ecuacion": regression.equation,
    }


def _csv_response(frame: pd.DataFrame, filename: str):
    content = frame.to_csv(index=False).encode("utf-8-sig")
    return send_file(
        io.BytesIO(content),
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name=filename,
    )


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/api/resumen")
def summary():
    origins = data_quality["origenes"]
    simulated_count = origins.get("Simulado", 0)
    if simulated_count == len(inspections):
        origin_label = "Simulado"
    elif simulated_count:
        origin_label = "Mixto"
    else:
        origin_label = "Registrado"
    return jsonify(
        _native(
            {
                "total_tramos": len(inspections),
                "pci_promedio": round(float(inspections["pci_observado"].mean()), 1),
                "antiguedad_media": round(float(inspections["antiguedad_anios"].mean()), 1),
                "origen_datos": origin_label,
                "datos_simulados": simulated_count,
                "metricas": regression.metrics(),
                "calidad_datos": data_quality,
            }
        )
    )


@app.get("/api/datos")
def data():
    records = inspections.to_dict(orient="records")
    ages = range(int(regression.min_age), int(regression.max_age) + 1)
    line = [{"x": age, "y": round(regression.m * age + regression.b, 2)} for age in ages]
    return jsonify(
        _native(
            {
                "registros": records,
                "puntos": [
                    {"x": row["antiguedad_anios"], "y": row["pci_observado"]}
                    for row in records
                ],
                "recta": line,
                "metricas": regression.metrics(),
                "calidad_datos": data_quality,
            }
        )
    )


@app.get("/api/tramos")
def segments():
    records = inspections[
        ["tramo_id", "via", "lat", "lng", "pci_observado", "antiguedad_anios"]
    ].rename(columns={"pci_observado": "pci"}).to_dict(orient="records")
    return jsonify(_native(records))


@app.post("/api/predecir")
def predict():
    current_age, horizon = _prediction_payload()
    return jsonify(_native(_prediction_result(current_age, horizon)))


@app.get("/api/patologias")
def list_pathologies():
    return jsonify(_native(_registry()))


@app.post("/api/patologias")
def register_pathology():
    segment_id = request.form.get("tramo_id", "").strip()
    pathology = request.form.get("patologia", "").strip()
    severity = request.form.get("severidad", "").strip()
    description = request.form.get("descripcion", "").strip()
    photo = request.files.get("foto")

    if not segment_id or len(segment_id) > 40:
        abort(422, description="Indica un tramo válido.")
    if segment_id not in known_segment_ids:
        abort(422, description="El tramo_id no existe en el dataset de inspecciones.")
    if pathology not in PATOLOGIAS:
        abort(422, description="Tipo de patología no válido.")
    if severity not in SEVERIDADES:
        abort(422, description="Nivel de severidad no válido.")
    if len(description) > 1000:
        abort(422, description="La descripción no puede superar 1000 caracteres.")
    if photo is None or not photo.filename:
        abort(422, description="Adjunta una fotografía de evidencia.")

    extension = ALLOWED_IMAGE_TYPES.get(photo.mimetype or "")
    if extension is None:
        abort(415, description="La fotografía debe ser JPG, PNG o WEBP.")

    records = _registry()
    content = bytearray()
    while chunk := photo.stream.read(64 * 1024):
        content.extend(chunk)
        if len(content) > MAX_UPLOAD_BYTES:
            abort(413, description="La imagen supera el límite de 8 MB.")
    if not content or not IMAGE_SIGNATURES[photo.mimetype](content):
        abort(415, description="El contenido del archivo no coincide con una imagen válida.")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{extension}"
    target = UPLOAD_DIR / filename
    try:
        target.write_bytes(content)
    except OSError:
        if target.exists():
            target.unlink()
        abort(500, description="No se pudo guardar la fotografía.")
    record = {
        "id": uuid.uuid4().hex,
        "tramo_id": segment_id,
        "patologia": pathology,
        "severidad": severity,
        "descripcion": description,
        "foto_url": f"/static/uploads/{filename}",
        "fecha": datetime.now(timezone.utc).isoformat(),
    }
    records.insert(0, record)

    temporary_registry = REGISTRY_PATH.with_suffix(".tmp")
    try:
        REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary_registry.write_text(
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_registry.replace(REGISTRY_PATH)
    except OSError:
        if target.exists():
            target.unlink()
        if temporary_registry.exists():
            temporary_registry.unlink()
        abort(500, description="No se pudo guardar el registro de patologías.")
    return jsonify(record), 201


@app.get("/api/exportar-csv")
def export_inspections_csv():
    return _csv_response(inspections, "inspecciones_pci_huanuco.csv")


@app.get("/api/exportar-patologias-csv")
def export_pathologies_csv():
    columns = ["id", "tramo_id", "patologia", "severidad", "descripcion", "foto_url", "fecha"]
    records = _registry()
    return _csv_response(pd.DataFrame(records, columns=columns), "patologias_huanuco.csv")


@app.post("/api/exportar-prediccion-csv")
def export_prediction_csv():
    current_age, horizon = _prediction_payload()
    result = _prediction_result(current_age, horizon)
    warning = (
        "Fuera del rango observado; aumenta la incertidumbre de la proyección."
        if result["extrapolacion"]
        else "Dentro del rango histórico observado."
    )
    row = {
        "antiguedad_actual_anios": current_age,
        "horizonte_anios": horizon,
        "antiguedad_futura_anios": result["antiguedad_futura"],
        "pci_actual": result["actual"]["pci"],
        "condicion_actual": result["actual"]["estado"],
        "pci_futuro": result["futuro"]["pci"],
        "condicion_futura": result["futuro"]["estado"],
        "extrapolacion": result["extrapolacion"],
        "advertencia": warning,
        "origen_datos_modelo": (
            f"{data_quality['origenes'].get('Simulado', 0)} de {len(inspections)} filas simuladas"
            if data_quality["origenes"].get("Simulado", 0)
            else "Dataset registrado; origen no verificado"
        ),
    }
    return _csv_response(pd.DataFrame([row]), "proyeccion_pci_huanuco.csv")


if __name__ == "__main__":
    app.run(debug=True, port=8000)
