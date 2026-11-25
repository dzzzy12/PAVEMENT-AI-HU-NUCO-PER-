from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT_DIR / "data" / "inspecciones_pci.csv"
MINIMUM_MODEL_ROWS = 50

PATOLOGIAS = [
    "Grietas longitudinales",
    "Grietas transversales",
    "Grietas de esquina",
    "Losa dividida",
    "Despostillamiento",
    "Escalonamiento",
]
SEVERIDADES = ["Baja", "Media", "Alta"]
TRANSITOS = ["Bajo", "Medio", "Alto"]

VIAS = [
    ("Jr. 28 de Julio", -9.9302, -76.2410),
    ("Av. Universitaria", -9.9514, -76.2394),
    ("Malecón Daniel Alomía Robles", -9.9271, -76.2390),
    ("Carretera Central", -9.9230, -76.2690),
    ("Av. Alameda de la República", -9.9260, -76.2425),
    ("Av. Esteban Pavletich", -9.9425, -76.2458),
    ("Jr. Dos de Mayo", -9.9295, -76.2380),
    ("Av. Circunvalación", -9.9440, -76.2560),
    ("Amarilis · Av. Perú", -9.9365, -76.2300),
    ("Pillco Marca · Av. Juan Velasco", -9.9680, -76.2500),
]


def _generate_dataset() -> pd.DataFrame:
    """Genera observaciones explícitamente simuladas, ubicadas en Huánuco."""
    rng = np.random.default_rng(2026)
    rows = []
    start_date = pd.Timestamp("2024-01-15")

    for index in range(80):
        via, base_lat, base_lng = VIAS[index % len(VIAS)]
        age = int(rng.integers(1, 16))
        traffic = str(rng.choice(TRANSITOS, p=[0.28, 0.46, 0.26]))
        pathology = str(rng.choice(PATOLOGIAS))
        severity = str(rng.choice(SEVERIDADES, p=[0.37, 0.42, 0.21]))
        traffic_penalty = {"Bajo": 0, "Medio": 4, "Alto": 9}[traffic]
        severity_penalty = {"Baja": 0, "Media": 4, "Alta": 9}[severity]
        pathology_penalty = {
            "Grietas longitudinales": 1,
            "Grietas transversales": 2,
            "Grietas de esquina": 5,
            "Losa dividida": 9,
            "Despostillamiento": 4,
            "Escalonamiento": 6,
        }[pathology]
        pci = np.clip(
            98 - age * 3.35 - traffic_penalty - severity_penalty
            - pathology_penalty + rng.normal(0, 7),
            2,
            100,
        )
        rows.append(
            {
                "tramo_id": f"HCO-{index + 1:03d}",
                "via": via,
                "fecha": (start_date + pd.Timedelta(days=int(index * 9))).date().isoformat(),
                "antiguedad_anios": age,
                "nivel_transito": traffic,
                "patologia_principal": pathology,
                "severidad": severity,
                "pci_observado": round(float(pci), 1),
                "lat": round(base_lat + rng.uniform(-0.0032, 0.0032), 6),
                "lng": round(base_lng + rng.uniform(-0.0040, 0.0040), 6),
                "origen_datos": "Simulado",
            }
        )

    return pd.DataFrame(rows)


def _clean_dataset(dataset: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Normaliza columnas y excluye filas inválidas o inspecciones duplicadas."""
    if "nivel_transito" not in dataset.columns and "transito_imda" in dataset.columns:
        dataset = dataset.rename(columns={"transito_imda": "nivel_transito"})
    elif "nivel_transito" in dataset.columns and "transito_imda" in dataset.columns:
        dataset = dataset.copy()
        current = dataset["nivel_transito"].astype("string").str.strip().replace("", pd.NA)
        legacy = dataset["transito_imda"].astype("string").str.strip().replace("", pd.NA)
        dataset["nivel_transito"] = current.fillna(legacy)
        dataset = dataset.drop(columns=["transito_imda"])

    required = [
        "tramo_id", "via", "fecha", "antiguedad_anios", "nivel_transito",
        "patologia_principal", "severidad", "pci_observado", "lat", "lng",
    ]
    missing_columns = sorted(set(required).difference(dataset.columns))
    if missing_columns:
        raise ValueError(
            "El CSV de inspecciones no contiene columnas obligatorias: "
            f"{', '.join(missing_columns)}. Se admite transito_imda como nombre legado."
        )

    frame = dataset.copy()
    frame["tramo_id"] = frame["tramo_id"].astype("string").str.strip().replace("", pd.NA)
    frame["via"] = frame["via"].astype("string").str.strip().replace("", pd.NA)

    parsed_dates = pd.to_datetime(frame["fecha"], errors="coerce", utc=True)
    frame["fecha"] = parsed_dates.dt.strftime("%Y-%m-%d")
    for column in ("antiguedad_anios", "pci_observado", "lat", "lng"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    category_specs = {
        "nivel_transito": TRANSITOS,
        "patologia_principal": PATOLOGIAS,
        "severidad": SEVERIDADES,
    }
    for column, categories in category_specs.items():
        canonical = {value.casefold(): value for value in categories}
        values = frame[column].astype("string").str.strip().str.casefold()
        frame[column] = values.map(canonical)

    empty_required = frame[required].isna().any(axis=1)
    invalid_age = (
        ~np.isfinite(frame["antiguedad_anios"].fillna(np.nan))
        | frame["antiguedad_anios"].lt(0)
    )
    invalid_pci = (
        ~np.isfinite(frame["pci_observado"].fillna(np.nan))
        | ~frame["pci_observado"].between(0, 100)
    )
    invalid_coordinates = (
        ~np.isfinite(frame["lat"].fillna(np.nan))
        | ~np.isfinite(frame["lng"].fillna(np.nan))
        | ~frame["lat"].between(-90, 90)
        | ~frame["lng"].between(-180, 180)
    )
    invalid_reasons = {
        "campos_vacios_o_categorias_no_validas": empty_required,
        "antiguedad_invalida": invalid_age,
        "pci_invalido": invalid_pci,
        "coordenadas_invalidas": invalid_coordinates,
    }
    invalid_rows = pd.concat(invalid_reasons, axis=1).any(axis=1)
    invalid_count = int(invalid_rows.sum())
    frame = frame.loc[~invalid_rows].copy()

    duplicate_rows = frame.duplicated(subset=["tramo_id", "fecha"], keep="first")
    duplicate_count = int(duplicate_rows.sum())
    frame = frame.loc[~duplicate_rows].copy()

    if "origen_datos" not in frame.columns:
        frame["origen_datos"] = "Registrado"
    else:
        origins = frame["origen_datos"].astype("string").str.strip().replace("", pd.NA).fillna("Registrado")
        canonical_origins = {"simulado": "Simulado", "registrado": "Registrado"}
        frame["origen_datos"] = origins.map(
            lambda value: canonical_origins.get(value.casefold(), value)
        )

    frame["antiguedad_anios"] = frame["antiguedad_anios"].astype(float)
    frame["pci_observado"] = frame["pci_observado"].astype(float)
    frame["lat"] = frame["lat"].astype(float)
    frame["lng"] = frame["lng"].astype(float)
    frame = frame[required + ["origen_datos"]].reset_index(drop=True)

    report = {
        "filas_entrada": int(len(dataset)),
        "filas_validas": int(len(frame)),
        "filas_invalidas_excluidas": invalid_count,
        "duplicados_tramo_fecha_excluidos": duplicate_count,
        "filas_excluidas": invalid_count + duplicate_count,
        "motivos": {
            name: int(mask.sum())
            for name, mask in invalid_reasons.items()
            if mask.any()
        },
        "origenes": {
            str(name): int(count)
            for name, count in frame["origen_datos"].value_counts().items()
        },
    }
    return frame, report


def load_inspections() -> tuple[pd.DataFrame, dict]:
    """Carga, valida y devuelve inspecciones junto con el resumen de limpieza."""
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DATA_PATH.exists():
        try:
            dataset = pd.read_csv(DATA_PATH)
        except pd.errors.EmptyDataError as exc:
            raise ValueError(
                "El CSV de inspecciones está vacío; se requieren al menos "
                f"{MINIMUM_MODEL_ROWS} filas válidas para entrenar y evaluar."
            ) from exc
    else:
        dataset = _generate_dataset()
        dataset.to_csv(DATA_PATH, index=False)

    cleaned, report = _clean_dataset(dataset)
    if len(cleaned) < MINIMUM_MODEL_ROWS:
        raise ValueError(
            "No hay suficientes inspecciones válidas para entrenar y evaluar el modelo: "
            f"se requieren al menos {MINIMUM_MODEL_ROWS}; quedan {len(cleaned)} "
            f"tras excluir {report['filas_excluidas']} filas "
            f"({report['filas_invalidas_excluidas']} inválidas y "
            f"{report['duplicados_tramo_fecha_excluidos']} duplicadas por tramo y fecha)."
        )
    return cleaned, report
