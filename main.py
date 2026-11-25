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
SEVERITY_PCI_ADJUSTMENT = {"Baja": 2, "Media": 6, "Alta": 12}
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


def _save_registry(records: list[dict]) -> None:
    temporary_registry = REGISTRY_PATH.with_name(
        f"{REGISTRY_PATH.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary_registry.write_text(
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_registry.replace(REGISTRY_PATH)
    except OSError:
        app.logger.exception("No se pudo guardar el registro de patologías")
        try:
            temporary_registry.unlink(missing_ok=True)
        except OSError:
            app.logger.exception("No se pudo limpiar el archivo temporal de patologías")
        abort(500, description="No se pudo guardar el registro de patologías.")


def _registry() -> list[dict]:
    if not REGISTRY_PATH.exists():
        return []
    try:
        records = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        abort(500, description="No se pudo leer el registro de patologías.")
    if not isinstance(records, list):
        abort(500, description="El registro de patologías tiene un formato inválido.")
    if any(not isinstance(record, dict) for record in records):
        abort(500, description="El registro de patologías tiene un formato inválido.")

    changed = False
    seen_ids = set()
    for record in records:
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id.strip() or record_id in seen_ids:
            record_id = uuid.uuid4().hex
            while record_id in seen_ids:
                record_id = uuid.uuid4().hex
            record["id"] = record_id
            changed = True
        seen_ids.add(record_id)
        segment_id = record.get("tramo_id")
        severity = record.get("severidad")
        has_estimate = (
            isinstance(record.get("pci_estimado"), (int, float))
            and math.isfinite(float(record["pci_estimado"]))
            and isinstance(record.get("condicion"), str)
        )
        if (
            not has_estimate
            and isinstance(segment_id, str)
            and segment_id in known_segment_ids
            and isinstance(severity, str)
            and severity in SEVERITY_PCI_ADJUSTMENT
        ):
            estimate = _pathology_pci_estimate(segment_id, severity)
            record["pci_estimado"] = estimate["pci_estimado"]
            record["condicion"] = estimate["condicion"]
            changed = True
    if changed:
        _save_registry(records)
    return records


def _image_path(record: dict) -> Path | None:
    photo_url = record.get("foto_url")
    prefix = "/static/uploads/"
    if not isinstance(photo_url, str) or not photo_url.startswith(prefix):
        return None
    filename = photo_url[len(prefix):]
    if not filename or "/" in filename or "\\" in filename or Path(filename).name != filename:
        return None
    upload_root = UPLOAD_DIR.resolve()
    path = (UPLOAD_DIR / filename).resolve()
    return path if path.parent == upload_root else None


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


def _pathology_pci_estimate(segment_id: str, severity: str) -> dict:
    segment_inspections = inspections.loc[inspections["tramo_id"] == segment_id]
    if segment_inspections.empty:
        abort(422, description="El tramo_id no existe en el dataset de inspecciones.")
    if severity not in SEVERITY_PCI_ADJUSTMENT:
        abort(422, description="Nivel de severidad no válido.")

    latest_inspection = segment_inspections.sort_values("fecha").iloc[-1]
    age = float(latest_inspection["antiguedad_anios"])
    base_pci = regression.predict(age)["pci"]
    estimated_pci = round(
        float(np.clip(base_pci - SEVERITY_PCI_ADJUSTMENT[severity], 0, 100)),
        1,
    )
    return {
        "tramo_id": segment_id,
        "antiguedad_anios": age,
        "pci_base": base_pci,
        "ajuste_severidad": -SEVERITY_PCI_ADJUSTMENT[severity],
        "pci_estimado": estimated_pci,
        "condicion": regression.classify(estimated_pci),
        "nota": "Estimación referencial basada en antigüedad y severidad; no sustituye una evaluación técnica PCI.",
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


@app.post("/api/estimar-pci-patologia")
def estimate_pathology_pci():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(400, description="Envía tramo_id y severidad como JSON.")
    segment_id = payload.get("tramo_id")
    severity = payload.get("severidad")
    if not isinstance(segment_id, str) or not segment_id.strip():
        abort(422, description="Indica un tramo válido.")
    if not isinstance(severity, str):
        abort(422, description="Nivel de severidad no válido.")
    severity = severity.strip()
    if severity not in SEVERITY_PCI_ADJUSTMENT:
        abort(422, description="Nivel de severidad no válido.")
    return jsonify(_native(_pathology_pci_estimate(segment_id.strip(), severity)))


@app.get("/api/patologias")
def list_pathologies():
    return jsonify(_native(_registry()))


@app.post("/api/patologias")
def register_pathology():
    segment_id = request.form.get("tramo_id", "").strip()
    pathology = (
        request.form.get("patologia", "").strip()
        or request.form.get("patologia_sugerida", "").strip()
    )
    severity = (
        request.form.get("severidad", "").strip()
        or request.form.get("severidad_sugerida", "").strip()
    )
    description = request.form.get("descripcion", "").strip()
    photo = request.files.get("foto")

    if not segment_id or len(segment_id) > 40:
        abort(422, description="Indica un tramo válido.")
    if segment_id not in known_segment_ids:
        abort(422, description="El tramo_id no existe en el dataset de inspecciones.")
    if not pathology:
        abort(422, description="Selecciona o confirma un tipo de patología.")
    if pathology not in PATOLOGIAS:
        abort(422, description="Tipo de patología no válido.")
    if not severity:
        abort(422, description="Selecciona o confirma el nivel de severidad.")
    if severity not in SEVERIDADES:
        abort(422, description="Nivel de severidad no válido.")
    if len(description) > 1000:
        abort(422, description="La descripción no puede superar 1000 caracteres.")
    if photo is None or not photo.filename:
        abort(422, description="Adjunta una fotografía de evidencia.")

    pci_estimate = _pathology_pci_estimate(segment_id, severity)
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
        "pci_estimado": pci_estimate["pci_estimado"],
        "condicion": pci_estimate["condicion"],
    }
    records.insert(0, record)

    try:
        _save_registry(records)
    except HTTPException:
        if target.exists():
            try:
                target.unlink()
            except OSError:
                app.logger.exception("No se pudo limpiar la fotografía tras un fallo de registro")
        raise
    return jsonify(record), 201


@app.delete("/api/patologias/<registro_id>")
def delete_pathology(registro_id: str):
    records = _registry()
    record_index = next(
        (index for index, record in enumerate(records) if record.get("id") == registro_id),
        None,
    )
    if record_index is None:
        abort(404, description="No se encontró el registro de patología.")

    record = records[record_index]
    image_path = _image_path(record)
    image_backup = None
    if image_path is not None and image_path.is_file():
        try:
            image_backup = image_path.read_bytes()
            image_path.unlink()
        except OSError:
            app.logger.exception("No se pudo eliminar la fotografía del registro %s", registro_id)
            abort(500, description="No se pudo eliminar la fotografía del registro.")

    updated_records = records[:record_index] + records[record_index + 1:]
    try:
        _save_registry(updated_records)
    except HTTPException:
        if image_backup is not None and image_path is not None:
            try:
                image_path.write_bytes(image_backup)
            except OSError:
                app.logger.exception("No se pudo restaurar la fotografía del registro %s", registro_id)
        raise
    return jsonify({"id": registro_id, "eliminado": True})


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
