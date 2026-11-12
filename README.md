# Sistema IoT de alerta temprana para un puente

Proyecto final — Computación Física e Internet de las Cosas.

Un ESP32 vigila un puente sobre un río: mide el **nivel del río** con un sensor ultrasónico HC-SR04 y la **inclinación de la estructura** con un MPU6050. Si hay riesgo, un operador puede **cerrar la barrera del puente** (servo) a distancia.

## Arquitectura

| Componente | Herramienta | Qué hace |
|---|---|---|
| Circuito | Wokwi | ESP32 + HC-SR04 + MPU6050 + servo (barrera) + 2 LED de alerta |
| Telemetría | MQTT → ThingsBoard | Publica `nivel_rio_cm` e `inclinacion` en JSON en `v1/devices/me/telemetry` cada 3 s |
| Telecontrol | MQTT ← ThingsBoard | Recibe comandos en `v1/devices/me/rpc/request/+` y abre/cierra la barrera |
| Histórico | InfluxDB Cloud | Bucket `puente_iot`, measurement `puente`, cada 10 s |
| Visualización | Grafana Cloud | Un panel por variable y exportación a CSV |
| Análisis | Streamlit Cloud | App que carga el CSV de Grafana y analiza el estado del puente |

## Variables y umbrales

| Variable | Unidad | Rango | Umbral de alerta |
|---|---|---|---|
| Nivel del río (`nivel_rio_cm`) | cm | 0 – 400 | ≥ 250 cm (creciente) |
| Inclinación (`inclinacion`) | grados | 0 – 90 | > 10° (riesgo estructural) |

## Enlaces

- Simulación en Wokwi:  https://wokwi.com/projects/480505327512785921
- App en Streamlit Cloud: (https://proyecto-final-iot-hxx7cqxg4dprrayx7quqjh.streamlit.app)
- Dashboard de Grafana: ver capturas en `datos/`

## Contenido del repositorio

- `sketch.ino`, `diagram.json` y `libraries.txt` — código del ESP32, circuito y librerías de Wokwi. Los tokens están reemplazados por `PEGA_AQUI_...`.
- `app.py` y `requirements.txt` — app de Streamlit.
- `datos/` — CSV exportados de los paneles de Grafana que usa la app.
## Correr la app en local

```bash
pip install -r requirements.txt
streamlit run app.py
```
