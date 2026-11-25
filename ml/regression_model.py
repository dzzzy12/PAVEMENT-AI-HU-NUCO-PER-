from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupShuffleSplit

from ml.data_loader import MINIMUM_MODEL_ROWS


@dataclass
class PavementRegressionModel:
    """Regresión lineal simple: antigüedad del pavimento → PCI observado."""

    data: pd.DataFrame

    def __post_init__(self) -> None:
        if len(self.data) < MINIMUM_MODEL_ROWS:
            raise ValueError(
                "Se requieren al menos "
                f"{MINIMUM_MODEL_ROWS} inspecciones válidas para separar entrenamiento y prueba."
            )
        if self.data["tramo_id"].nunique() < 2:
            raise ValueError(
                "Se requieren al menos dos tramos distintos para separar entrenamiento y prueba "
                "sin mezclar inspecciones del mismo tramo."
            )
        self.model = LinearRegression()
        x = self.data[["antiguedad_anios"]].astype(float)
        y = self.data["pci_observado"].astype(float)
        splitter = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42)
        train_indices, test_indices = next(
            splitter.split(x, y, groups=self.data["tramo_id"])
        )
        x_train, x_test = x.iloc[train_indices], x.iloc[test_indices]
        y_train, y_test = y.iloc[train_indices], y.iloc[test_indices]
        self.model.fit(x_train, y_train)
        test_predictions = self.model.predict(x_test)
        self.m = float(self.model.coef_[0])
        self.b = float(self.model.intercept_)
        self.r2 = float(r2_score(y_test, test_predictions))
        self.mae = float(mean_absolute_error(y_test, test_predictions))
        self.rmse = float(np.sqrt(mean_squared_error(y_test, test_predictions)))
        self.min_age = float(x["antiguedad_anios"].min())
        self.max_age = float(x["antiguedad_anios"].max())
        self.equation = f"PCI = {self.m:.2f} × antigüedad {self.b:+.2f}"

    @staticmethod
    def classify(pci: float) -> str:
        if pci > 85:
            return "Bueno"
        if pci > 70:
            return "Satisfactorio"
        if pci > 55:
            return "Regular"
        if pci > 40:
            return "Pobre"
        if pci > 25:
            return "Muy pobre"
        if pci > 10:
            return "Grave"
        return "Fallado"

    def predict(self, age: float) -> dict[str, float | str | bool]:
        raw_pci = float(self.model.predict(pd.DataFrame({"antiguedad_anios": [age]}))[0])
        pci = float(np.clip(raw_pci, 0, 100))
        return {
            "antiguedad_anios": float(age),
            "pci": round(pci, 1),
            "pci_sin_limitar": round(raw_pci, 1),
            "estado": self.classify(pci),
            "extrapolacion": age < self.min_age or age > self.max_age,
        }

    def metrics(self) -> dict[str, float | str]:
        return {
            "m": round(self.m, 4),
            "b": round(self.b, 4),
            "ecuacion": self.equation,
            "r2": round(self.r2, 4),
            "mae": round(self.mae, 2),
            "rmse": round(self.rmse, 2),
            "antiguedad_min": self.min_age,
            "antiguedad_max": self.max_age,
        }
