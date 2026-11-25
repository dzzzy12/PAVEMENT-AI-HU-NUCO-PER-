"use strict";

const state = { records: [], map: null, chart: null, summary: null, metrics: null, lastPrediction: null };
const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[char]));

async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `Error ${response.status}`;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch { /* The response may not contain JSON. */ }
    throw new Error(message);
  }
  return response.json();
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("visible");
  window.setTimeout(() => toast.classList.remove("visible"), 3000);
}

function pciColor(pci) {
  if (pci > 70) return "#10b981";
  if (pci > 40) return "#f59e0b";
  return "#ef4444";
}

function conditionClass(name) {
  if (name === "Bueno" || name === "Satisfactorio") return "good";
  if (name === "Regular") return "fair";
  return "poor";
}

function setCondition(element, label) {
  element.textContent = label;
  const color = pciColor(({ Bueno: 93, Satisfactorio: 78, Regular: 63, Pobre: 48, "Muy pobre": 33, Grave: 18, Fallado: 5 })[label] || 5);
  element.style.color = color;
  element.style.borderColor = `${color}55`;
  element.style.background = `${color}15`;
}

function renderKpis(summary) {
  animateCounter($("#kpi-segments .counter"), summary.total_tramos, 0);
  animateCounter($("#kpi-pci .counter"), summary.pci_promedio, 1);
  animateCounter($("#kpi-age .counter"), summary.antiguedad_media, 1);
  animateCounter($("#kpi-r2 .counter"), summary.metricas.r2, 2);
  setCondition($("#pci-state"), PavementState(summary.pci_promedio));
  const simulated = Number(summary.datos_simulados || 0);
  const total = Number(summary.total_tramos || 0);
  const originMessage = simulated === total
    ? `Dataset demostrativo: ${simulated} filas simuladas. No son mediciones técnicas reales ni inspecciones verificadas en campo.`
    : simulated > 0
      ? `Dataset mixto: ${simulated} de ${total} filas simuladas; el resto se cargó como registrado. El origen no certifica una medición técnica.`
      : `Dataset registrado: ${total} filas cargadas. La aplicación no verifica si son mediciones técnicas reales.`;
  $("#simulated-banner-text").textContent = originMessage;
  const quality = summary.calidad_datos;
  $("#quality-summary").textContent =
    `Validación: ${quality.filas_entrada} filas leídas; ${quality.filas_validas} válidas; `
    + `${quality.filas_invalidas_excluidas} inválidas y `
    + `${quality.duplicados_tramo_fecha_excluidos} duplicadas (mismo tramo y fecha) excluidas.`;
  $("#dataset-total").textContent = `${quality.filas_validas} FILAS`;
}

function animateCounter(element, target, decimals) {
  const end = Number(target);
  const duration = 1100;
  const startTime = performance.now();
  const formatter = new Intl.NumberFormat("es-PE", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  const update = (now) => {
    const progress = Math.min((now - startTime) / duration, 1);
    const eased = 1 - (1 - progress) ** 3;
    element.textContent = formatter.format(end * eased);
    if (progress < 1) requestAnimationFrame(update);
  };
  requestAnimationFrame(update);
}

function PavementState(pci) {
  if (pci > 85) return "Bueno";
  if (pci > 70) return "Satisfactorio";
  if (pci > 55) return "Regular";
  if (pci > 40) return "Pobre";
  if (pci > 25) return "Muy pobre";
  if (pci > 10) return "Grave";
  return "Fallado";
}

function initMap(segments) {
  if (!state.map) {
    state.map = L.map("map", { zoomControl: true, scrollWheelZoom: false }).setView([-9.932, -76.242], 13);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      subdomains: "abc",
      maxZoom: 19,
    }).addTo(state.map);
  }
  const markers = [];
  let selectedMarker = null;
  segments.forEach((segment) => {
    const pci = Number(segment.pci);
    const color = pciColor(pci);
    const isCritical = pci <= 40;
    const marker = L.marker([segment.lat, segment.lng], {
      icon: L.divIcon({
        className: "map-marker-icon",
        html: `<span class="map-marker${isCritical ? " critical-marker" : ""}" style="--marker-color:${color}" aria-label="PCI ${esc(pci)}"><span class="marker-core"></span></span>`,
        iconSize: [22, 22],
        iconAnchor: [11, 11],
      }),
    }).bindPopup(`<div class="popup-title">${esc(segment.tramo_id)} · PCI ${esc(segment.pci)}</div><div class="popup-meta">${esc(segment.via)}<br>Antigüedad: ${esc(segment.antiguedad_anios)} años</div>`);
    marker.on("click", () => {
      selectedMarker?.getElement()?.querySelector(".map-marker")?.classList.remove("selected-marker");
      marker.getElement()?.querySelector(".map-marker")?.classList.add("selected-marker");
      selectedMarker = marker;
    });
    marker.addTo(state.map);
    markers.push(marker);
  });
  $("#map-count").textContent = `${segments.length} PUNTOS`;
  $("#locate-map").addEventListener("click", () => state.map.setView([-9.932, -76.242], 13));
  return markers;
}

function updatePredictionChart(data, result) {
  const current = { x: Number(result.actual.antiguedad_anios), y: Number(result.actual.pci) };
  const future = { x: Number(result.futuro.antiguedad_anios), y: Number(result.futuro.pci) };
  const line = data.recta.map((point) => ({ x: Number(point.x), y: Number(point.y) }));
  const projection = [current, future];
  if (state.chart) {
    state.chart.data.datasets[0].data = data.puntos;
    state.chart.data.datasets[1].data = line;
    state.chart.data.datasets[2].data = projection;
    state.chart.update();
    return;
  }
  state.chart = new Chart($("#prediction-chart"), {
    type: "scatter",
    data: { datasets: [
      { label: "PCI observado", data: data.puntos, backgroundColor: "rgba(6,182,212,.58)", borderColor: "#06b6d4", pointRadius: 3, pointHoverRadius: 5 },
      { label: "Regresión lineal", data: line, showLine: true, pointRadius: 0, borderColor: "#f59e0b", borderWidth: 2, tension: 0 },
      { label: "Proyección", data: projection, showLine: true, pointRadius: 5, pointBackgroundColor: ["#06b6d4", "#f59e0b"], pointBorderColor: "#111827", pointBorderWidth: 2, borderColor: "#f59e0b", borderDash: [4, 4], borderWidth: 1.5 },
    ] },
    options: {
      responsive: true, maintainAspectRatio: false, animation: { duration: 250 },
      plugins: { legend: { display: false }, tooltip: { backgroundColor: "#111827", borderColor: "#334155", borderWidth: 1, titleColor: "#dce6f1", bodyColor: "#aebccc", callbacks: { label: (context) => ` Edad ${context.parsed.x} años · PCI ${context.parsed.y.toFixed(1)}` } } },
      scales: {
        x: { type: "linear", title: { display: false }, grid: { color: "#202b39" }, ticks: { color: "#77879b", font: { size: 9 }, maxTicksLimit: 8 }, border: { display: false } },
        y: { min: 0, max: 100, grid: { color: "#202b39" }, ticks: { color: "#77879b", font: { size: 9 }, stepSize: 25 }, border: { display: false } },
      },
    },
  });
}

async function runPrediction() {
  const age = Number($("#age-input").value);
  const horizon = Number($("#horizon-input").value);
  if (!Number.isFinite(age) || age < 0 || age > 100 || !Number.isFinite(horizon) || horizon < 0 || horizon > 50) {
    throw new Error("Ingresa una antigüedad de 0–100 años y un horizonte de 0–50 años.");
  }
  const result = await api("/api/predecir", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ antiguedad_actual: age, horizonte_anios: horizon }),
  });
  $("#prediction-current").textContent = result.actual.pci.toFixed(1);
  $("#prediction-future").textContent = result.futuro.pci.toFixed(1);
  $("#prediction-current").classList.remove("result-pop");
  $("#prediction-future").classList.remove("result-pop");
  requestAnimationFrame(() => {
    $("#prediction-current").classList.add("result-pop");
    $("#prediction-future").classList.add("result-pop");
  });
  setCondition($("#prediction-current-state"), result.actual.estado);
  setCondition($("#prediction-future-state"), result.futuro.estado);
  $("#equation").textContent = result.ecuacion;
  $("#extrapolation-alert").hidden = !result.extrapolacion;
  updatePredictionChart(state.chartData, result);
  state.lastPrediction = result;
  return result;
}

function renderMetrics(metrics) {
  $("#metric-r2").textContent = Number(metrics.r2).toFixed(3);
  $("#metric-mae").textContent = Number(metrics.mae).toFixed(2);
  $("#metric-rmse").textContent = Number(metrics.rmse).toFixed(2);
  const direction = metrics.m < 0 ? "disminuye" : "aumenta";
  $("#slope-explanation").textContent =
    `por cada año adicional, el PCI estimado ${direction} ${Math.abs(metrics.m).toFixed(2)} puntos en promedio`;
  const min = Math.max(0, Math.round(metrics.antiguedad_min));
  const max = Math.round(metrics.antiguedad_max);
  const ages = [min, Math.round((min + max) / 2), max + 4];
  const titles = ["Tramo nuevo", "Tramo intermedio", "Tramo crítico"];
  const notes = [
    "Superficie de corta edad dentro del periodo observado.",
    "Antigüedad representativa del rango histórico.",
    "Fuera del máximo histórico: la extrapolación se indica con cautela.",
  ];
  const colors = ["#10b981", "#f59e0b", "#ef4444"];
  $("#scenario-grid").innerHTML = ages.map((age, index) => {
    const raw = metrics.m * age + metrics.b;
    const score = Math.max(0, Math.min(100, raw));
    const stateName = PavementState(score);
    return `<article class="scenario-card" style="--scenario-color:${colors[index]}"><h3>${titles[index]}</h3><div class="scenario-age">${age} AÑOS ${index === 2 ? "· EXTRAPOLACIÓN" : "· DENTRO DE RANGO"}</div><div class="scenario-score">${score.toFixed(1)} <small style="font-size:10px;color:#79879a">PCI</small></div><span class="condition-chip" style="color:${colors[index]};border-color:${colors[index]}55;background:${colors[index]}15">${stateName}</span><p class="scenario-note">${notes[index]}</p></article>`;
  }).join("");
}

function renderDataset(records) {
  const query = $("#dataset-search").value.trim().toLocaleLowerCase("es");
  const filtered = records.filter((row) =>
    [row.tramo_id, row.via, row.patologia_principal, row.nivel_transito, row.severidad]
      .some((value) => String(value).toLocaleLowerCase("es").includes(query)));
  $("#dataset-body").innerHTML = filtered.map((row) => `<tr>
    <td>${esc(row.tramo_id)}</td><td>${esc(row.via)}</td><td>${esc(row.fecha)}</td>
    <td>${esc(row.antiguedad_anios)} años</td><td>${esc(row.nivel_transito)}</td>
    <td>${esc(row.patologia_principal)}</td><td>${esc(row.severidad)}</td>
    <td class="pci-cell" style="color:${pciColor(Number(row.pci_observado))}">${Number(row.pci_observado).toFixed(1)}</td>
    <td><span class="origin-tag">${esc(row.origen_datos || "Registrado")}</span></td></tr>`).join("");
  $("#table-count").textContent = `${filtered.length} DE ${records.length} REGISTROS`;
}

function renderPathologies(records) {
  $("#report-count").textContent = `${records.length} REPORTE${records.length === 1 ? "" : "S"}`;
  if (!records.length) {
    $("#pathology-gallery").innerHTML = '<div class="empty-state"><i data-lucide="images"></i><strong>Aún no hay reportes</strong><span>Los hallazgos que registres aparecerán aquí.</span></div>';
    window.lucide?.createIcons();
    return;
  }
  $("#pathology-gallery").innerHTML = records.map((record) => {
    const date = new Date(record.fecha).toLocaleDateString("es-PE", { day: "2-digit", month: "short", year: "numeric" });
    const severity = String(record.severidad).toLocaleLowerCase("es");
    return `<article class="report-card"><img class="report-photo" src="${esc(record.foto_url)}" alt="Evidencia: ${esc(record.patologia)}" loading="lazy"><div class="report-info"><div class="report-title"><span>${esc(record.patologia)}</span><span class="severity-badge severity-${esc(severity)}">${esc(record.severidad)}</span></div><div class="report-id">${esc(record.tramo_id)}</div>${record.descripcion ? `<p class="report-desc">${esc(record.descripcion)}</p>` : ""}<div class="report-date">${esc(date)}</div></div></article>`;
  }).join("");
}

async function initialize() {
  $("#current-date").textContent = new Date().toLocaleDateString("es-PE", { day: "2-digit", month: "short", year: "numeric" }).toUpperCase();
  const [summary, chartData, segments, pathologies] = await Promise.all([
    api("/api/resumen"), api("/api/datos"), api("/api/tramos"), api("/api/patologias"),
  ]);
  state.summary = summary;
  state.metrics = chartData.metricas;
  state.chartData = chartData;
  state.records = chartData.registros;
  renderKpis(summary);
  initMap(segments);
  renderMetrics(chartData.metricas);
  renderDataset(chartData.registros);
  renderPathologies(pathologies);
  $("#segment-options").innerHTML = segments.map((segment) => `<option value="${esc(segment.tramo_id)}">${esc(segment.via)}</option>`).join("");
  await runPrediction();
  window.lucide?.createIcons();
}

document.querySelectorAll(".nav-tab").forEach((tab) => tab.addEventListener("click", () => {
  document.querySelectorAll(".nav-tab").forEach((item) => item.classList.toggle("active", item === tab));
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${tab.dataset.tab}`));
  if (tab.dataset.tab === "dashboard" && state.map) window.setTimeout(() => state.map.invalidateSize(), 50);
}));

$("#prediction-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try { await runPrediction(); } catch (error) { showToast(error.message); }
});
$("#export-prediction").addEventListener("click", async () => {
  const age = Number($("#age-input").value);
  const horizon = Number($("#horizon-input").value);
  try {
    const response = await fetch("/api/exportar-prediccion-csv", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ antiguedad_actual: age, horizonte_anios: horizon }),
    });
    if (!response.ok) {
      let message = `Error ${response.status}`;
      try { message = (await response.json()).detail || message; } catch { /* Response may not be JSON. */ }
      throw new Error(message);
    }
    const blob = await response.blob();
    const downloadUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = "proyeccion_pci_huanuco.csv";
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000);
  } catch (error) {
    showToast(error.message);
  }
});
$("#age-input").addEventListener("input", () => {
  if ($("#age-input").value !== "") runPrediction().catch((error) => showToast(error.message));
});
$("#horizon-input").addEventListener("input", () => {
  $("#horizon-value").textContent = `${$("#horizon-input").value} años`;
  runPrediction().catch((error) => showToast(error.message));
});
$("#dataset-search").addEventListener("input", () => renderDataset(state.records));
$("#print-report").addEventListener("click", () => window.print());
$("#pathology-form").elements.foto.addEventListener("change", (event) => {
  const file = event.target.files[0];
  $("#upload-label").innerHTML = file ? `<u>${esc(file.name)}</u> · ${(file.size / (1024 * 1024)).toFixed(2)} MB` : "Arrastra una imagen o <u>explora archivos</u>";
});
$("#pathology-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const message = $("#pathology-message");
  message.classList.remove("error");
  message.textContent = "Guardando inspección...";
  try {
    await api("/api/patologias", { method: "POST", body: new FormData(form) });
    renderPathologies(await api("/api/patologias"));
    form.reset();
    $("#upload-label").innerHTML = "Arrastra una imagen o <u>explora archivos</u>";
    message.textContent = "Inspección guardada correctamente.";
    showToast("Patología registrada con evidencia fotográfica.");
  } catch (error) {
    message.textContent = error.message;
    message.classList.add("error");
  }
});

initialize().catch((error) => {
  console.error("No se pudo inicializar PAVEMENT AI:", error);
  showToast(`Error al cargar los datos: ${error.message}`);
});
