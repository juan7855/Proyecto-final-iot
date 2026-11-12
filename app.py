"""
Proyecto final IoT — Análisis del sistema de alerta temprana del puente
Carga el CSV exportado desde Grafana y analiza el nivel del río y la inclinación.
"""
import io
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

# ---------------- Parámetros del sistema (los mismos del ESP32) ----------------
ALTURA_SENSOR_CM = 400.0   # el sensor está a 400 cm del lecho del río
NIVEL_ALERTA_CM = 250.0    # creciente: el río supera 250 cm
INCLINACION_MAX = 10.0     # riesgo estructural: inclinación mayor a 10°

VARIABLES = {
    "nivel_rio_cm": {"nombre": "Nivel del río", "unidad": "cm",
                      "umbral": NIVEL_ALERTA_CM, "rango": (0, ALTURA_SENSOR_CM)},
    "inclinacion": {"nombre": "Inclinación del puente", "unidad": "°",
                     "umbral": INCLINACION_MAX, "rango": (0, 90)},
}
CSV_EJEMPLO = Path(__file__).parent / "datos" / "grafana_export.csv"

st.set_page_config(page_title="Monitoreo del puente", page_icon="🌉", layout="wide")


# ---------------- Lectura del CSV de Grafana ----------------
def leer_csv(contenido: bytes) -> pd.DataFrame:
    texto = contenido.decode("utf-8-sig", errors="replace")
    lineas = texto.splitlines()
    if lineas and lineas[0].lower().startswith("sep="):   # CSV "para Excel" de Grafana
        texto = "\n".join(lineas[1:])
    df = pd.read_csv(io.StringIO(texto), sep=None, engine="python")
    df.columns = [str(c).strip().strip('"') for c in df.columns]
    return df


def preparar(df: pd.DataFrame) -> pd.DataFrame:
    """Encuentra la columna de tiempo y las dos variables, sin importar cómo las nombre Grafana."""
    col_tiempo = next((c for c in df.columns if c.lower() in ("time", "_time", "tiempo", "timestamp")),
                      df.columns[0])
    renombrar = {col_tiempo: "tiempo"}
    for c in df.columns:
        nombre = c.lower()
        if "nivel" in nombre:
            renombrar[c] = "nivel_rio_cm"
        elif "inclin" in nombre:
            renombrar[c] = "inclinacion"
    df = df.rename(columns=renombrar)

    t = df["tiempo"]
    if pd.api.types.is_numeric_dtype(t):          # Grafana a veces exporta milisegundos (UTC)
        unidad = "ms" if t.max() > 1e11 else "s"
        tiempo = pd.to_datetime(t, unit=unidad, utc=True).dt.tz_convert("America/Bogota")
    else:
        tiempo = pd.to_datetime(t, errors="coerce", format="mixed")
        if getattr(tiempo.dt, "tz", None) is not None:   # trae zona horaria -> hora de Colombia
            tiempo = tiempo.dt.tz_convert("America/Bogota")
        # sin zona horaria: Grafana ya la exportó en la hora local del navegador
    tiempo = tiempo.dt.tz_localize(None) if getattr(tiempo.dt, "tz", None) else tiempo
    df["tiempo"] = tiempo.astype("datetime64[ns]")

    for v in VARIABLES:
        if v in df.columns:
            df[v] = pd.to_numeric(df[v].astype(str).str.replace(r"[^0-9.\-]", "", regex=True),
                                  errors="coerce")
    columnas = ["tiempo"] + [v for v in VARIABLES if v in df.columns]
    return df[columnas].dropna(subset=["tiempo"]).sort_values("tiempo")


def unir(tablas: list[pd.DataFrame]) -> pd.DataFrame:
    """Si suben un CSV por panel, los une por tiempo."""
    df = tablas[0]
    for otra in tablas[1:]:
        df = pd.merge_asof(df.sort_values("tiempo"), otra.sort_values("tiempo"),
                           on="tiempo", direction="nearest", tolerance=pd.Timedelta("15s"),
                           suffixes=("", "_dup"))
        df = df[[c for c in df.columns if not c.endswith("_dup")]]
    return df


# ---------------- Interfaz ----------------
st.title("🌉 Monitoreo del puente: nivel del río e inclinación")
st.caption("Análisis histórico de los datos que el ESP32 envía a InfluxDB y se exportan desde Grafana.")

with st.sidebar:
    st.header("Datos")
    archivos = st.file_uploader("Sube el CSV exportado de Grafana", type=["csv"],
                                accept_multiple_files=True)
    st.header("Umbrales de operación")
    umbral_nivel = st.number_input("Alerta de nivel (cm)", 0.0, ALTURA_SENSOR_CM,
                                   NIVEL_ALERTA_CM, step=10.0)
    umbral_inc = st.number_input("Inclinación máxima (°)", 0.0, 90.0, INCLINACION_MAX, step=1.0)
    VARIABLES["nivel_rio_cm"]["umbral"] = umbral_nivel
    VARIABLES["inclinacion"]["umbral"] = umbral_inc

if archivos:
    tablas = [preparar(leer_csv(a.getvalue())) for a in archivos]
    origen = ", ".join(a.name for a in archivos)
elif CSV_EJEMPLO.exists():
    tablas = [preparar(leer_csv(CSV_EJEMPLO.read_bytes()))]
    origen = f"{CSV_EJEMPLO.name} (incluido en el repositorio)"
else:
    st.info("Sube el CSV que descargaste del panel de Grafana para empezar.")
    st.stop()

df = unir(tablas)
presentes = [v for v in VARIABLES if v in df.columns]
if not presentes:
    st.error("No encontré las columnas nivel_rio_cm ni inclinacion en el CSV. "
             f"Columnas recibidas: {list(tablas[0].columns)}")
    st.stop()

st.success(f"{len(df)} registros cargados desde {origen}, "
           f"del {df['tiempo'].min():%d/%m %H:%M} al {df['tiempo'].max():%d/%m %H:%M}.")

# ---------------- 1. Estado actual ----------------
st.subheader("1. Estado actual")
cols = st.columns(len(presentes) + 1)
alertas_activas = []
for col, v in zip(cols, presentes):
    info = VARIABLES[v]
    serie = df[v].dropna()
    ultimo = serie.iloc[-1]
    en_alerta = ultimo >= info["umbral"] if v == "nivel_rio_cm" else ultimo > info["umbral"]
    if en_alerta:
        alertas_activas.append(info["nombre"])
    col.metric(info["nombre"], f"{ultimo:.1f} {info['unidad']}",
               f"{ultimo - serie.iloc[0]:+.1f} {info['unidad']} en el periodo", delta_color="inverse")
estado = "🔴 EN ALERTA: " + ", ".join(alertas_activas) if alertas_activas else "🟢 Operación normal"
cols[-1].metric("Estado del puente", "Alerta" if alertas_activas else "Normal")
cols[-1].write(estado)

# ---------------- 2. Comportamiento en el tiempo ----------------
st.subheader("2. Comportamiento en el tiempo")
for v in presentes:
    info = VARIABLES[v]
    datos = df[["tiempo", v]].dropna()
    base = alt.Chart(datos).encode(x=alt.X("tiempo:T", title="Hora"))
    linea = base.mark_line(point=True).encode(
        y=alt.Y(f"{v}:Q", title=f"{info['nombre']} ({info['unidad']})"),
        tooltip=[alt.Tooltip("tiempo:T", title="Hora", format="%H:%M:%S"),
                 alt.Tooltip(f"{v}:Q", title=info["nombre"], format=".1f")])
    umbral = alt.Chart(pd.DataFrame({"u": [info["umbral"]]})).mark_rule(
        color="red", strokeDash=[6, 4]).encode(y="u:Q")
    st.altair_chart((linea + umbral).properties(height=260, title=info["nombre"]),
                    width="stretch")
    st.caption(f"Línea roja: umbral de alerta ({info['umbral']:.0f} {info['unidad']}).")

# ---------------- 3. Estadísticas ----------------
st.subheader("3. Estadísticas del periodo")
filas = []
for v in presentes:
    info = VARIABLES[v]
    s = df[v].dropna()
    fuera = (s >= info["umbral"]) if v == "nivel_rio_cm" else (s > info["umbral"])
    filas.append({
        "Variable": f"{info['nombre']} ({info['unidad']})",
        "Mínimo": round(s.min(), 1), "Máximo": round(s.max(), 1),
        "Promedio": round(s.mean(), 1), "Desv. estándar": round(s.std(), 2),
        "Lecturas": len(s),
        "% del tiempo en alerta": round(100 * fuera.mean(), 1),
    })
st.dataframe(pd.DataFrame(filas), hide_index=True, width="stretch")

# ---------------- 4. Alertas por umbral ----------------
st.subheader("4. Eventos de alerta")
eventos = []
for v in presentes:
    info = VARIABLES[v]
    d = df[["tiempo", v]].dropna().copy()
    d["alerta"] = (d[v] >= info["umbral"]) if v == "nivel_rio_cm" else (d[v] > info["umbral"])
    d["grupo"] = (d["alerta"] != d["alerta"].shift()).cumsum()
    for _, g in d[d["alerta"]].groupby("grupo"):
        eventos.append({
            "Variable": info["nombre"],
            "Inicio": g["tiempo"].min().strftime("%H:%M:%S"),
            "Fin": g["tiempo"].max().strftime("%H:%M:%S"),
            "Duración (s)": int((g["tiempo"].max() - g["tiempo"].min()).total_seconds()),
            "Valor máximo": f"{g[v].max():.1f} {info['unidad']}",
        })
if eventos:
    st.warning(f"Se detectaron {len(eventos)} eventos de alerta en el periodo.")
    st.dataframe(pd.DataFrame(eventos), hide_index=True, width="stretch")
else:
    st.success("No hubo alertas en el periodo analizado.")

# ---------------- 5. Relación entre variables ----------------
if len(presentes) == 2:
    st.subheader("5. Relación entre nivel del río e inclinación")
    d = df.dropna(subset=presentes)
    if len(d) > 2 and d[presentes].std().min() > 0:
        r = d["nivel_rio_cm"].corr(d["inclinacion"])
        fuerza = "fuerte" if abs(r) >= 0.7 else "moderada" if abs(r) >= 0.4 else "débil"
        st.write(f"Correlación de Pearson: **{r:.2f}** (relación {fuerza}). "
                 "Una correlación alta indicaría que la creciente está afectando la estructura.")
        st.altair_chart(alt.Chart(d).mark_circle(size=60).encode(
            x=alt.X("nivel_rio_cm:Q", title="Nivel del río (cm)"),
            y=alt.Y("inclinacion:Q", title="Inclinación (°)"),
            tooltip=["tiempo:T", "nivel_rio_cm:Q", "inclinacion:Q"]).properties(height=300),
            width="stretch")
    else:
        st.info("Una de las variables no cambió en el periodo; no se puede calcular la correlación.")

# ---------------- 6. Recomendación para el operador ----------------
st.subheader("6. Recomendación para el operador")
if alertas_activas:
    st.error("Cerrar la barrera del puente desde el panel de control y enviar inspección.")
elif eventos:
    st.warning("Hubo alertas en el periodo: revisar el puente aunque ahora esté normal.")
else:
    st.success("El puente operó dentro de los rangos seguros. No se requiere acción.")

with st.expander("Ver datos cargados"):
    st.dataframe(df, hide_index=True, width="stretch")
