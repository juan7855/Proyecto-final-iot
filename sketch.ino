/*
  PROYECTO FINAL IoT — Sistema de alerta temprana para un puente
  ESP32 (Wokwi) + HC-SR04 (nivel del río) + MPU6050 (inclinación) + servo (barrera)

  Telemetría : publica nivel_rio_cm e inclinacion en JSON por MQTT (ThingsBoard)
  Telecontrol: recibe comandos RPC por MQTT y mueve la barrera (servo)
  Histórico  : guarda las dos variables con marca de tiempo en InfluxDB
*/

#include <WiFi.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <ESP32Servo.h>
#include <InfluxDbClient.h>
#include <InfluxDbCloud.h>

// ---------------- WIFI (no cambiar en Wokwi) ----------------
#define WIFI_SSID     "Wokwi-GUEST"
#define WIFI_PASSWORD ""

// ---------------- THINGSBOARD (MQTT) ----------------
const char* TB_SERVER = "mqtt.thingsboard.cloud";
const int   TB_PORT   = 1883;
const char* TB_TOKEN  = "PEGA_AQUI_TOKEN_THINGSBOARD";   // Access token del dispositivo

const char* TOPICO_DATOS   = "v1/devices/me/telemetry";      // aquí publica
const char* TOPICO_CONTROL = "v1/devices/me/rpc/request/+";  // aquí recibe comandos

// ---------------- INFLUXDB ----------------
#define INFLUXDB_URL    "https://us-east-1-1.aws.cloud2.influxdata.com"
#define INFLUXDB_TOKEN  "PEGA_AQUI_TOKEN_INFLUXDB"
#define INFLUXDB_ORG    "06e9b1ef2d027859"
#define INFLUXDB_BUCKET "puente_iot"
#define TZ_INFO         "<-05>5"   // Colombia (UTC-5)

// ---------------- PINES ----------------
const int PIN_TRIG    = 5;    // HC-SR04 TRIG
const int PIN_ECHO    = 18;   // HC-SR04 ECHO
const int PIN_LED_NIV = 26;   // LED azul: alerta de nivel
const int PIN_LED_INC = 33;   // LED rojo: alerta de inclinación
const int PIN_SERVO   = 13;   // Servo: barrera de cierre del puente
// MPU6050 por I2C: SDA = 21, SCL = 22

// ---------------- PARÁMETROS DEL SISTEMA ----------------
const float ALTURA_SENSOR_CM = 400.0;  // distancia del sensor al lecho del río
const float NIVEL_ALERTA_CM  = 250.0;  // umbral de creciente
const float INCLINACION_MAX  = 10.0;   // grados permitidos
const unsigned long INTERVALO_MQTT   = 3000;   // ms entre publicaciones MQTT
const unsigned long INTERVALO_INFLUX = 10000;  // ms entre escrituras en InfluxDB

WiFiClient espClient;
PubSubClient mqtt(espClient);
InfluxDBClient influx(INFLUXDB_URL, INFLUXDB_ORG, INFLUXDB_BUCKET, INFLUXDB_TOKEN);
Point punto("puente");
Adafruit_MPU6050 mpu;
Servo barrera;

bool barreraCerrada = false;
float nivel = 0, inclinacion = 0;
unsigned long ultimoMqtt = 0, ultimoInflux = 0;

void publicarTelemetria();   // declarada más abajo

// ---------------- ACTUADOR ----------------
void moverBarrera(bool cerrar) {
  barreraCerrada = cerrar;
  barrera.write(cerrar ? 90 : 0);   // 90° = cerrada, 0° = abierta
  Serial.println(cerrar ? ">> Barrera CERRADA" : ">> Barrera ABIERTA");
}

// ---------------- COMANDOS (telecontrol) ----------------
// ThingsBoard envía: {"method":"setValue","params":true}  -> cerrar
//                    {"method":"setValue","params":false} -> abrir
//                    {"method":"getValue"}                -> pide el estado
void alRecibirComando(char* topic, byte* payload, unsigned int length) {
  String mensaje;
  for (unsigned int i = 0; i < length; i++) mensaje += (char)payload[i];
  Serial.print("Comando recibido: ");
  Serial.println(mensaje);

  String t = topic;
  String idPeticion = t.substring(t.lastIndexOf('/') + 1);

  if (mensaje.indexOf("setValue") >= 0) {
    moverBarrera(mensaje.indexOf("true") >= 0);
    publicarTelemetria();   // el tablero ve el cambio al instante
  }

  // Respuesta para que el interruptor del tablero muestre el estado real
  String respuesta = barreraCerrada ? "true" : "false";
  String topicoRespuesta = "v1/devices/me/rpc/response/" + idPeticion;
  mqtt.publish(topicoRespuesta.c_str(), respuesta.c_str());
}

// ---------------- CONEXIONES ----------------
void conectarWiFi() {
  Serial.print("Conectando a WiFi");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD, 6);
  while (WiFi.status() != WL_CONNECTED) {
    delay(250);
    Serial.print(".");
  }
  Serial.println("\nWiFi conectado");
}

void conectarMQTT() {
  while (!mqtt.connected()) {
    Serial.print("Conectando a ThingsBoard... ");
    String clientId = "esp32-puente-" + String(random(0xffff), HEX);
    if (mqtt.connect(clientId.c_str(), TB_TOKEN, NULL)) {
      Serial.println("conectado");
      mqtt.subscribe(TOPICO_CONTROL);
    } else {
      Serial.print("falló, rc=");
      Serial.print(mqtt.state());
      Serial.println(" reintentando en 3 s");
      delay(3000);
    }
  }
}

// ---------------- SENSORES ----------------
void leerSensores() {
  // Nivel del río con el HC-SR04
  digitalWrite(PIN_TRIG, LOW);  delayMicroseconds(2);
  digitalWrite(PIN_TRIG, HIGH); delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);
  long duracion = pulseIn(PIN_ECHO, HIGH, 30000);
  float distancia = duracion * 0.0343 / 2.0;
  nivel = (duracion == 0) ? 0 : constrain(ALTURA_SENSOR_CM - distancia, 0, ALTURA_SENSOR_CM);

  // Inclinación del puente con el MPU6050 (ángulo respecto a la vertical)
  sensors_event_t a, g, temp;
  mpu.getEvent(&a, &g, &temp);
  float ax = a.acceleration.x, ay = a.acceleration.y, az = a.acceleration.z;
  float total = sqrt(ax * ax + ay * ay + az * az);
  inclinacion = (total > 0) ? acos(constrain(az / total, -1.0, 1.0)) * 180.0 / PI : 0;

  // Alertas locales (funcionan aunque no haya internet)
  digitalWrite(PIN_LED_NIV, nivel >= NIVEL_ALERTA_CM);
  digitalWrite(PIN_LED_INC, inclinacion > INCLINACION_MAX);
}

void publicarTelemetria() {
  char json[160];
  snprintf(json, sizeof(json),
           "{\"nivel_rio_cm\":%.1f,\"inclinacion\":%.1f,\"barrera\":%d,"
           "\"alerta_nivel\":%s,\"alerta_inclinacion\":%s}",
           nivel, inclinacion, barreraCerrada ? 1 : 0,
           nivel >= NIVEL_ALERTA_CM ? "true" : "false",
           inclinacion > INCLINACION_MAX ? "true" : "false");
  bool ok = mqtt.publish(TOPICO_DATOS, json);
  Serial.print(ok ? "[MQTT OK] " : "[MQTT ERROR] ");
  Serial.println(json);
}

void guardarEnInflux() {
  punto.clearFields();
  punto.addField("nivel_rio_cm", nivel);
  punto.addField("inclinacion", inclinacion);
  punto.addField("barrera", barreraCerrada ? 1 : 0);
  if (influx.writePoint(punto)) {
    Serial.println("[INFLUX OK]");
  } else {
    Serial.print("[INFLUX ERROR] ");
    Serial.println(influx.getLastErrorMessage());
  }
}

// ---------------- SETUP Y LOOP ----------------
void setup() {
  Serial.begin(115200);
  pinMode(PIN_TRIG, OUTPUT);
  pinMode(PIN_ECHO, INPUT);
  pinMode(PIN_LED_NIV, OUTPUT);
  pinMode(PIN_LED_INC, OUTPUT);

  barrera.attach(PIN_SERVO, 500, 2400);
  moverBarrera(false);

  Wire.begin(21, 22);
  if (!mpu.begin()) {
    Serial.println("No se encontró el MPU6050. Revisa las conexiones.");
    while (true) delay(1000);
  }
  mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
  Serial.println("Sensores listos");

  conectarWiFi();
  timeSync(TZ_INFO, "pool.ntp.org", "time.nis.gov");

  influx.setInsecure();
  punto.addTag("device", "esp32-puente");
  if (influx.validateConnection()) Serial.println("Conectado a InfluxDB");
  else {
    Serial.print("Error InfluxDB: ");
    Serial.println(influx.getLastErrorMessage());
  }

  mqtt.setServer(TB_SERVER, TB_PORT);
  mqtt.setCallback(alRecibirComando);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) conectarWiFi();
  if (!mqtt.connected()) conectarMQTT();
  mqtt.loop();   // escucha los comandos de control

  unsigned long ahora = millis();

  if (ahora - ultimoMqtt >= INTERVALO_MQTT) {
    ultimoMqtt = ahora;
    leerSensores();
    publicarTelemetria();
  }

  if (ahora - ultimoInflux >= INTERVALO_INFLUX) {
    ultimoInflux = ahora;
    guardarEnInflux();
  }
}
