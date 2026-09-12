# OptiTraffic

[![GitHub](https://img.shields.io/badge/GitHub-asoto59g%2FOptiTraffic-181717?logo=github)](https://github.com/asoto59g/OptiTraffic)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/UI-Streamlit-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![SUMO](https://img.shields.io/badge/Simulator-SUMO-0B6E4F)](https://eclipse.dev/sumo/)
[![TomTom](https://img.shields.io/badge/Traffic-TomTom%20API-DF1B12)](https://developer.tomtom.com/)
[![OpenStreetMap](https://img.shields.io/badge/Map%20data-OpenStreetMap-7EBC6F?logo=openstreetmap&logoColor=white)](https://www.openstreetmap.org/copyright)
[![Geofabrik](https://img.shields.io/badge/Extracts-Geofabrik-1F4E79)](https://download.geofabrik.de/)
[![Last commit](https://img.shields.io/github/last-commit/asoto59g/OptiTraffic)](https://github.com/asoto59g/OptiTraffic/commits)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)](https://github.com/asoto59g/OptiTraffic)

Simulador de tráfico vehicular urbano para evaluar mejoras en **semáforos**, **sentidos**, **parqueo** y **señales de alto**, calibrado con tráfico real TomTom y red OSM.

**Stack:** Streamlit + Python + [SUMO](https://eclipse.dev/sumo/) + TomTom Traffic Flow (freemium) + OpenStreetMap (Overpass / Geofabrik).

**Repo:** https://github.com/asoto59g/OptiTraffic

---

## Requisitos

1. **Python 3.10+**
2. **SUMO** instalado y `SUMO_HOME` definido  
   - Windows (ejemplo): `C:\Program Files (x86)\Eclipse\Sumo`
   ```powershell
   $env:SUMO_HOME = "C:\Program Files (x86)\Eclipse\Sumo"
   $env:Path = "$env:SUMO_HOME\bin;$env:Path"
   ```
3. **Clave TomTom** (recomendado): [developer.tomtom.com](https://developer.tomtom.com/) — freemium (~200k vector tiles/mes)

---

## Instalación

```powershell
cd OptiTraffic
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Edite .env: TOMTOM_API_KEY=... y SUMO_HOME=...
```

## Ejecutar

```powershell
streamlit run app.py
# o: python -m streamlit run app.py
```

---

## Flujo del wizard (6 pasos)

| Paso | Qué hace |
|------|----------|
| **1. Zona** | Ciudad/país, geocodificación, rectángulo, dibujo o GeoJSON (máx. ~25 km²). Detecta lugar por reverse geocode. |
| **2. Red OSM→SUMO** | Overpass (rápido) o Geofabrik (extracto país) → recorte → `netconvert` → mapa de edges. |
| **3. Configuración** | Clic en **cruces** (semáforos) o **calles** (alto/parqueo/carriles). Tiempos G/Y/R. **Guardar/cargar** config ligada al polígono. |
| **4. TomTom** | Flow tiles → nivel relativo por edge (hora pico). |
| **5. Simulación** | Demanda calibrada + TraCI → KPIs. Escenarios Bajo/Medio/Alto (veh/h). |
| **6. Resultados** | Mapa de congestión, CSV/GeoJSON, guardar escenario completo. |

---

## Modelo de tráfico (parámetros urbanos)

Definidos en `src/traffic_params.py`:

| Parámetro | Valor |
|-----------|--------|
| Velocidad máxima urbana | **40 km/h** (~11.11 m/s) |
| Largo promedio vehículo | **5.0 m** + gap 2.5 m |
| Congestión (KPI) | media &lt; ~20 km/h |
| Capacidad | tope de escenario **y** capacidad física (Greenshields / espacio del tramo) |

**Densidad por escenario (por sentido):**

| Escenario | Por sentido | Ambos sentidos (ref.) |
|-----------|-------------|------------------------|
| Bajo | 150–200 veh/h | 300–400 |
| Medio | 250–350 veh/h | 500–700 |
| Alto | 400–500 veh/h | 800–1.000 |

En hora pico, TomTom **reduce la velocidad permitida del tramo** (no solo sube la demanda).

**Sentidos OSM:** se respetan `oneway` de OpenStreetMap. El mapa muestra un sentido (azul oscuro + flechas) vs doble sentido. Si OSM no trae `oneway=yes`, SUMO modela doble sentido.

---

## Datos OSM (Overpass / Geofabrik)

- **Auto:** Overpass primero; si falla → Geofabrik + osmium.
- **Solo Overpass:** ideal para polígonos pequeños.
- **Solo Geofabrik:** extracto de país (p. ej. Costa Rica). El país del paso 1 debe estar mapeado (`Costa Rica`, `México`, etc.).
- Rutas con acentos (OneDrive “Geomática”) se copian a `%LOCALAPPDATA%\OptiTraffic\` para herramientas nativas (netconvert, SUMO, osmium).

---

## Escenarios guardados

Al terminar la configuración (paso 3) o en resultados (paso 6):

- Se guarda **polígono + semáforos/altos/parqueos/carriles + red + edges** en `scenarios/<nombre>_fecha/`.
- Se puede **sobrescribir** por nombre.
- Carga desde el paso 3 (configs del mismo polígono, IoU ≥ 85%) o desde la **barra lateral**.
- Los escenarios locales no se suben a git (`scenarios/*/` en `.gitignore`).

---

## Estructura del proyecto

```
app.py                 # Wizard Streamlit
src/
  area.py              # Zona, geocode, GeoJSON
  osm_fetch.py         # Overpass / Geofabrik + clip
  network_build.py     # netconvert, edges GeoJSON, tope 40 km/h
  editors.py           # TLS / parking / stops / lanes → XML SUMO
  tomtom.py            # Flow tiles + match a edges
  demand.py            # Demanda + escenarios de densidad
  traffic_params.py    # 40 km/h, 5 m, capacidad espacial
  simulate.py          # TraCI, KPIs, cierre seguro de conexiones
  viz.py               # Folium: red, cruces, sentidos, congestión
  scenarios.py         # Guardar / cargar config ligada al polígono
  sumo_env.py          # Detección SUMO_HOME
data/                  # cache local (gitignored)
scenarios/             # escenarios locales (gitignored)
.env.example
requirements.txt
```

---

## Notas operativas

- **TraCI:** si aparece `Connection already active` o `Connection closed by SUMO`, recargue la app y cierre procesos `sumo.exe` huérfanos. Los semáforos adicionales usan fases reales del `.net.xml` (no estados inventados de longitud fija).
- **Mapa de configuración:** la lentitud al confirmar no depende de Geofabrik; se usa una red simplificada en Folium.
- Áreas grandes ralentizan `netconvert` y la simulación; mantenga el polígono pequeño en el MVP.
- No suba `.env` ni claves TomTom al repositorio.

---

## Changelog reciente (post-MVP inicial)

- Pipeline OSM: Overpass + Geofabrik con validación de PBF (rechazo de HTML 200).
- Rutas ASCII-safe en Windows (`%LOCALAPPDATA%\OptiTraffic`).
- Config interactiva por clic en cruces/calles; zoom de configuración.
- Guardar/cargar escenarios asociados al polígono.
- Modelo 40 km/h, vehículo 5 m, topes Bajo/Medio/Alto + capacidad espacial.
- TomTom reduce velocidad en presa; simulación TraCI más robusta.
- Visualización de sentidos OSM (flechas / colores).
- TLS additionals alineados al número de links del cruce.

---

## Licencia de datos

- Mapas: © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors.
- Extractos: [Geofabrik](https://download.geofabrik.de/).
- Tráfico en vivo: TomTom (según términos de su API).
