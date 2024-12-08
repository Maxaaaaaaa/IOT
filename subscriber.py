import json
import logging
import os
import time
from datetime import datetime
from typing import Dict, Any

import paho.mqtt.client as mqtt
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

# Конфигурация логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s - %(filename)s:%(lineno)d'
)


class MQTTConfig:
    """Конфигурация MQTT соединения"""
    BROKER = os.getenv('MQTT_BROKER', "jcddef63.ala.eu-central-1.emqxsl.com")
    PORT = int(os.getenv('MQTT_PORT', "8883"))
    USERNAME = os.getenv('MQTT_USERNAME', "mqtt_user")
    PASSWORD = os.getenv('MQTT_PASSWORD', "mqtt_pass")
    TOPICS = {
        'temperature': "sensor/temperature",
        'display': "display/text",
        'vision': "vision/objects"
    }
    CERT_PATH = "emqxsl-ca.crt"


class InfluxDBConfig:
    """Конфигурация InfluxDB"""
    URL = os.getenv('INFLUXDB_URL', "http://influxdb:8086")
    TOKEN = os.getenv('INFLUXDB_TOKEN', "your-token")
    ORG = os.getenv('INFLUXDB_ORG', "myorg")
    BUCKET = os.getenv('INFLUXDB_BUCKET', "sensor_data")


class DataProcessor:
    """Класс для обработки данных"""

    @staticmethod
    def process_temperature(data: Dict[str, Any]) -> Point:
        return Point("temperature_measurements") \
            .tag("sensor_type", "dallas") \
            .tag("device_id", data.get("device", "unknown")) \
            .field("temperature", float(data["temperature"])) \
            .time(datetime.utcnow())

    @staticmethod
    def process_display(data: Dict[str, Any]) -> Point:
        return Point("display_messages") \
            .tag("type", "lcd") \
            .field("message", data.get("text", "")) \
            .time(datetime.utcnow())

    @staticmethod
    def process_vision(data: Dict[str, Any]) -> Point:
        return Point("vision_data") \
            .tag("source", "camera") \
            .field("detected_objects", json.dumps(data.get("objects", {}))) \
            .time(datetime.utcnow())


class SensorDataCollector:
    """Основной класс для сбора и обработки данных с сенсоров"""

    def __init__(self):
        self._setup_metrics()
        self._init_influxdb()
        self._init_mqtt()
        self.data_processor = DataProcessor()

    def _setup_metrics(self):
        """Инициализация метрик"""
        self.metrics = {
            'messages_processed': 0,
            'database_writes': 0,
            'errors': 0
        }

    def _init_influxdb(self):
        """Инициализация подключения к InfluxDB"""
        try:
            self.influx_client = InfluxDBClient(
                url=InfluxDBConfig.URL,
                token=InfluxDBConfig.TOKEN,
                org=InfluxDBConfig.ORG
            )
            self.write_api = self.influx_client.write_api(write_options=SYNCHRONOUS)
            logging.info("InfluxDB connection established")
        except Exception as e:
            logging.error(f"InfluxDB initialization failed: {e}")
            raise

    def _init_mqtt(self):
        """Инициализация MQTT клиента"""
        try:

            self.mqtt_client = mqtt.Client()
            self.mqtt_client.username_pw_set(MQTTConfig.USERNAME, MQTTConfig.PASSWORD)
            self.mqtt_client.tls_set(ca_certs=MQTTConfig.CERT_PATH)

            self.mqtt_client.on_connect = self._on_connect
            self.mqtt_client.on_message = self._on_message
            self.mqtt_client.on_disconnect = self._on_disconnect

        except Exception as e:
            logging.error(f"Failed to initialize MQTT client: {e}")
            raise

    def _on_disconnect(self, client, userdata, rc, properties=None):
        """Обработчик отключения от MQTT брокера"""
        if rc != 0:
            logging.warning(f"Unexpected disconnection from MQTT broker with code: {rc}")
            self.metrics['errors'] += 1
            # Попытка переподключения
            try:
                self.mqtt_client.reconnect()
                logging.info("Successfully reconnected to MQTT broker")
            except Exception as e:
                logging.error(f"Failed to reconnect to MQTT broker: {e}")
        else:
            logging.info("Disconnected from MQTT broker")

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        """Обработчик подключения к MQTT"""
        if rc == 0:
            logging.info("Connected to MQTT broker successfully")
            for topic in MQTTConfig.TOPICS.values():
                client.subscribe(topic)
                logging.info(f"Subscribed to {topic}")
        else:
            logging.error(f"MQTT connection failed with code {rc}")

    def _on_message(self, client, userdata, msg):
        """Обработчик входящих сообщений"""
        try:
            payload = json.loads(msg.payload.decode())
            self.metrics['messages_processed'] += 1

            point = None
            if msg.topic == MQTTConfig.TOPICS['temperature']:
                point = self.data_processor.process_temperature(payload)
            elif msg.topic == MQTTConfig.TOPICS['display']:
                point = self.data_processor.process_display(payload)
            elif msg.topic == MQTTConfig.TOPICS['vision']:
                point = self.data_processor.process_vision(payload)

            if point and self._write_to_db(point):
                self.metrics['database_writes'] += 1

        except Exception as e:
            self.metrics['errors'] += 1
            logging.error(f"Message processing error: {e}")

    def _write_to_db(self, point: Point) -> bool:
        """Запись данных в базу"""
        try:
            self.write_api.write(bucket=InfluxDBConfig.BUCKET, record=point)
            return True
        except Exception as e:
            logging.error(f"Database write error: {e}")
            return False

    def start(self):
        """Запуск коллектора"""
        try:
            self.mqtt_client.connect(MQTTConfig.BROKER, MQTTConfig.PORT)
            self.mqtt_client.loop_start()

            while True:
                self._print_metrics()
                time.sleep(30)

        except KeyboardInterrupt:
            logging.info("Shutting down...")
        finally:
            self.cleanup()

    def _print_metrics(self):
        """Вывод метрик"""
        logging.info("=== Performance Metrics ===")
        for metric, value in self.metrics.items():
            logging.info(f"{metric.replace('_', ' ').title()}: {value}")

    def cleanup(self):
        """Очистка ресурсов"""
        self.mqtt_client.loop_stop()
        self.mqtt_client.disconnect()
        self.influx_client.close()


if __name__ == "__main__":
    collector = SensorDataCollector()
    collector.start()
