# OptiTraffic

[![GitHub](https://img.shields.io/badge/GitHub-asoto59g%2FOptiTraffic-181717?logo=github)](https://github.com/asoto59g/OptiTraffic)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/UI-Streamlit-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![SUMO](https://img.shields.io/badge/Simulator-SUMO-0B6E4F)](https://eclipse.dev/sumo/)
[![TomTom](https://img.shields.io/badge/Traffic-TomTom%20API-DF1B12)](https://developer.tomtom.com/)
[![OpenStreetMap](https://img.shields.io/badge/Map%20data-OpenStreetMap-7EBC6F?logo=openstreetmap&logoColor=white)](https://www.openstreetmap.org/copyright)
[![Geofabrik](https://img.shields.io/badge/Extracts-Geofabrik-1F4E79)](https://download.geofabrik.de/)
[![Last commit](https://img.shields.io/github/last-commit/asoto59g/OptiTraffic)](https://github.com/asoto59g/OptiTraffic/commits)
[![Issues](https://img.shields.io/github/issues/asoto59g/OptiTraffic)](https://github.com/asoto59g/OptiTraffic/issues)
[![Stars](https://img.shields.io/github/stars/asoto59g/OptiTraffic)](https://github.com/asoto59g/OptiTraffic/stargazers)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)](https://github.com/asoto59g/OptiTraffic)

Simulador de tráfico vehicular urbano para evaluar mejoras en semáforos, sentidos, parqueo y señales de alto.

**Stack:** Streamlit + Python + [SUMO](https://eclipse.dev/sumo/) + TomTom Traffic Flow (freemium) + OpenStreetMap (Geofabrik).

**Repo:** https://github.com/asoto59g/OptiTraffic

## Requisitos

1. **Python 3.10+**
2. **SUMO** instalado y variable `SUMO_HOME` definida
   - Windows (ejemplo): `C:\Program Files (x86)\Eclipse\Sumo`
   - En PowerShell (sesión actual):
     ```powershell
     $env:SUMO_HOME = "C:\Program Files (x86)\Eclipse\Sumo"
     $env:Path = "$env:SUMO_HOME\bin;$env:Path"
     ```
3. **Clave TomTom** (opcional pero recomendada): [developer.tomtom.com](https://developer.tomtom.com/) — freemium (~200k vector tiles/mes)

## Instalación

```powershell
cd OptiTraffic
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Edite .env y pegue TOMTOM_API_KEY=...
```

## Ejecutar

```powershell
streamlit run app.py
```

## Flujo MVP

1. **Zona** — ciudad/país + rectángulo, dibujo o GeoJSON (máx. ~25 km²)
2. **Red** — descarga Geofabrik → recorte → `netconvert` → mapa de edges
3. **Configuración** — semáforos (45/5/45 s), carriles, parqueo, altos
4. **TomTom** — flow tiles relativos → calibración de densidades por edge
5. **Simulación** — demanda + TraCI → KPIs de congestión
6. **Resultados** — mapa, CSV/GeoJSON, guardar escenario

## Estructura

```
app.py
src/
  area.py, osm_fetch.py, network_build.py, editors.py
  tomtom.py, demand.py, simulate.py, viz.py, scenarios.py, sumo_env.py
data/          # cache OSM, redes, runs
scenarios/     # escenarios guardados
```

## Notas

- Extractos Geofabrik se cachean en `data/osm/`.
- Sin `TOMTOM_API_KEY` la simulación usa densidad sintética uniforme.
- Áreas grandes pueden hacer lento `netconvert` y la simulación; mantenga el polígono pequeño en el MVP.
