---
layout: default
title: Dashboard
---

<div style="margin-bottom:1.5rem;">
  <label for="sensor-select"><strong>Sensor:</strong></label>
  <select id="sensor-select"></select>
  <span id="sensor-status" style="color:#6b7280; margin-left:.5rem;"></span>
</div>

<div style="display:grid; gap:1.5rem; grid-template-columns:1fr; max-width:1200px;">
  <div style="border:1px solid #e5e7eb; border-radius:12px; padding:1rem; box-shadow:0 2px 12px rgba(0,0,0,.05);">
    <h2 style="margin-top:0;">Forecast</h2>
    <div style="display:flex; justify-content:center; align-items:center; background:#fafafa; border-radius:8px; padding:1rem;">
      <img id="forecast-img" alt="Forecast PNG" style="max-width:100%; height:auto;" />
    </div>
  </div>
  <div style="border:1px solid #e5e7eb; border-radius:12px; padding:1rem; box-shadow:0 2px 12px rgba(0,0,0,.05);">
    <h2 style="margin-top:0;">Hindcast (1-day)</h2>
    <div style="display:flex; justify-content:center; align-items:center; background:#fafafa; border-radius:8px; padding:1rem;">
      <img id="hindcast-img" alt="Hindcast PNG" style="max-width:100%; height:auto;" />
    </div>
  </div>
</div>

<script>
  // Keep this list in sync with your sensors.csv street/city & slug naming.
  // slug must match the suffix used by your pipelines when saving PNGs:
  //   pm25_forecast_<slug>.png
  //   pm25_hindcast_<slug>.png
  const SENSORS = [
    { city: "vienna", street: "schottenfeldgasse", slug: "schottenfeldgasse" },
    { city: "vienna", street: "webgasse",         slug: "webgasse" },
    // Add more sensors here...
  ];

  // Base path where your images are stored, relative to docs root
  const BASE_IMG_PATH = "{{ '/air-quality/assets/img' | relative_url }}";

  const selectEl = document.getElementById("sensor-select");
  const forecastImg = document.getElementById("forecast-img");
  const hindcastImg = document.getElementById("hindcast-img");
  const statusEl = document.getElementById("sensor-status");

  // Populate dropdown
  SENSORS.forEach((s, index) => {
    const opt = document.createElement("option");
    opt.value = s.slug;
    opt.textContent = `${s.city} / ${s.street}`;
    if (index === 0) opt.selected = true;
    selectEl.appendChild(opt);
  });

  function updateImages() {
    const slug = selectEl.value;
    const t = new Date().getTime(); // cache-buster so browser sees fresh PNGs
    const forecastUrl = `${BASE_IMG_PATH}/pm25_forecast_${slug}.png?t=${t}`;
    const hindcastUrl = `${BASE_IMG_PATH}/pm25_hindcast_${slug}.png?t=${t}`;
    forecastImg.src = forecastUrl;
    hindcastImg.src = hindcastUrl;
    const sensor = SENSORS.find(s => s.slug === slug);
    if (sensor) {
      statusEl.textContent = `Showing: ${sensor.city} / ${sensor.street}`;
    } else {
      statusEl.textContent = `Showing: ${slug}`;
    }
  }

  selectEl.addEventListener("change", updateImages);
  updateImages();
</script>
