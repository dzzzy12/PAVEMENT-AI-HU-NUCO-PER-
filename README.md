# PAVEMENT AI · Huánuco

Aplicación web Flask para visualizar el estado del pavimento y estimar el Índice de Condición del Pavimento (PCI) con `LinearRegression` de scikit-learn. Conserva la interfaz HTML/CSS/JavaScript e incluye mapa, simulador, registro fotográfico, métricas y exportaciones CSV.

> **Datos demostrativos:** `data/inspecciones_pci.csv` contiene 80 filas sintéticas reproducibles, georreferenciadas alrededor de Huánuco y etiquetadas `Simulado`. No son inspecciones reales ni mediciones técnicas PCI verificadas. La aplicación conserva esa etiqueta en el mapa, el resumen, la tabla y los CSV.

## Requisitos

- Python 3.10 o superior
- pip
- Conexión a Internet para cargar Leaflet, Chart.js, Lucide, fuentes y mosaicos públicos del mapa.

## Instalación y ejecución

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

Abre <http://127.0.0.1:8000>.

## Dataset y validación

El archivo `data/inspecciones_pci.csv` se genera automáticamente si no existe. Las columnas canónicas son:

`tramo_id`, `via`, `fecha`, `antiguedad_anios`, `nivel_transito`, `patologia_principal`, `severidad`, `pci_observado`, `lat`, `lng`, `origen_datos`.

Por compatibilidad, al importar un CSV legado se acepta `transito_imda` y se normaliza en memoria a `nivel_transito`. La validación normaliza fechas y valores numéricos; excluye filas con campos obligatorios vacíos, fechas/categorías inválidas, antigüedad negativa, PCI fuera de 0–100 o coordenadas fuera de rango. Conserva la primera fila cuando se repite la combinación de `tramo_id` y `fecha`; inspecciones del mismo tramo en fechas distintas siguen siendo válidas. Las filas excluidas no se borran del archivo original. El resumen de validación está disponible en `GET /api/resumen` y en la pestaña Dataset.

Se requieren al menos 50 filas válidas para separar y evaluar el modelo. Si no se alcanza ese mínimo, el inicio falla con un mensaje que informa cuántas filas quedaron y cuántas se excluyeron.

## Modelo y alcance

La regresión usa únicamente `antiguedad_anios` como entrada y `pci_observado` como salida. Mantiene una partición reproducible 80/20 por grupos de `tramo_id` con `GroupShuffleSplit` (`random_state=42`), para evitar que inspecciones del mismo tramo aparezcan en entrenamiento y prueba. R², MAE y RMSE se calculan sobre prueba. El MAE expresa el error absoluto medio en puntos PCI. La pendiente describe el cambio lineal promedio estimado por año, no causalidad ni una tasa garantizada. La edad por sí sola no determina el estado del pavimento. Si se incorporan inspecciones repetidas por tramo, este corte agrupado las mantiene juntas.

Las predicciones se limitan visualmente a 0–100 y señalan las edades fuera del rango observado. La extrapolación aumenta la incertidumbre; ningún resultado reemplaza una inspección de campo o una evaluación técnica PCI.

## Registro de patologías y exportaciones

Las patologías se guardan en `data/patologias_reg.json`; las fotografías JPG, PNG y WEBP se almacenan en `static/uploads/`, hasta 8 MB. El registro solo acepta un `tramo_id` presente en el dataset.

- `GET /api/exportar-csv` — descarga el dataset validado.
- `GET /api/exportar-patologias-csv` — descarga el registro de patologías.
- `POST /api/exportar-prediccion-csv` — descarga la proyección; recibe el mismo JSON que el simulador: `{"antiguedad_actual": 8, "horizonte_anios": 5}`.

## API

- `GET /api/resumen` — KPIs, métricas y reporte de limpieza.
- `GET /api/datos` — filas normalizadas, puntos, recta y métricas.
- `GET /api/tramos` — coordenadas y PCI para el mapa.
- `POST /api/predecir` — calcula PCI actual y futuro.
- `GET /api/patologias` y `POST /api/patologias` — lista y registra reportes multipart con fotografía.

El mapa usa mosaicos públicos de OpenStreetMap con un filtro CSS oscuro y atribución visible. Los mosaicos Carto sin clave disponibles en el entorno devolvieron una imagen “API KEY REQUIRED”; esta alternativa evita mostrar ese error.
