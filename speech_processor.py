import numpy as np
import whisper
import threading
import queue
import time
import re
import sounddevice as sd
import json
import logging
import sys
import torch
import paho.mqtt.client as mqtt
import os
from datetime import datetime
from collections import deque

# Конфигурация MQTT
MQTT_BROKER = os.getenv('MQTT_BROKER', "jcddef63.ala.eu-central-1.emqxsl.com")
MQTT_PORT = int(os.getenv('MQTT_PORT', "8883"))
MQTT_USERNAME = os.getenv('MQTT_USERNAME', "mqtt_user")
MQTT_PASSWORD = os.getenv('MQTT_PASSWORD', "mqtt_pass")
MQTT_CERT_PATH = os.getenv('MQTT_CERT_PATH', "emqxsl-ca.crt")
MQTT_TOPIC = "display/text"

# Аудио конфигурация
CHANNELS = 1
RATE = 16000
CHUNK = 1024 * 2  # Увеличиваем размер чанка
BUFFER_SECONDS = 5  # Увеличиваем буфер


class AudioProcessor:
    def __init__(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            stream=sys.stdout
        )

        self.setup_mqtt()

        logging.info("Загрузка модели Whisper...")
        self.model = whisper.load_model("base")  # Используем базовую модель для начала
        logging.info("Модель загружена.")

        self.audio_queue = queue.Queue()
        self.running = True
        self.last_process_time = time.time()

        # Настройка устройства
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        logging.info(f"Используется устройство: {self.device}")

    def setup_mqtt(self):
        """Настройка MQTT клиента"""
        self.mqtt_client = mqtt.Client()
        self.mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        self.mqtt_client.tls_set(ca_certs=MQTT_CERT_PATH)

        # Добавляем обработчики
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_disconnect = self._on_disconnect

        try:
            self.mqtt_client.connect(MQTT_BROKER, MQTT_PORT)
            self.mqtt_client.loop_start()
            logging.info("MQTT подключение установлено")
        except Exception as e:
            logging.error(f"Ошибка подключения к MQTT: {e}")

    def _on_connect(self, client, userdata, flags, rc):
        """Обработчик подключения к MQTT"""
        if rc == 0:
            logging.info("Подключено к MQTT брокеру")
        else:
            logging.error(f"Ошибка подключения к MQTT брокеру: {rc}")

    def _on_disconnect(self, client, userdata, rc):
        """Обработчик отключения от MQTT"""
        logging.warning(f"Отключено от MQTT брокера: {rc}")
        if rc != 0:
            logging.info("Попытка переподключения...")

    def clean_text(self, text):
        # Добавляем польские символы в регулярное выражение
        text = re.sub(r'[^а-яА-Яa-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ0-9.,!? ]', '', text)
        words = text.split()
        return ' '.join(word for i, word in enumerate(words)
                        if i == 0 or word != words[i - 1])
    def is_silence(self, audio_data, threshold=0.01):
        return np.abs(audio_data).max() < threshold

    def audio_callback(self, indata, frames, time, status):
        if status:
            logging.warning(f"Status: {status}")
        try:
            self.audio_queue.put(indata.copy())
        except Exception as e:
            logging.error(f"Ошибка в callback: {e}")

    def transcribe_audio(self, audio_data):
        try:
            result = self.model.transcribe(
                audio_data,
                language="pl",
                fp16=(self.device == "cuda"),
                temperature=0.2
            )

            text = self.clean_text(result["text"]).strip()
            if text:
                # Формируем сообщение в точном соответствии с форматом ESP8266
                message = {
                    "text": text,  # Только текст, без timestamp
                }

                # Отладочная информация
                logging.info(f"Отправляем сообщение: {json.dumps(message)}")

                # Публикуем сообщение
                mqtt_result = self.mqtt_client.publish(
                    MQTT_TOPIC,
                    json.dumps(message),
                    qos=1,
                    retain=True  # Сохраняем последнее сообщение
                )

                if mqtt_result.rc == mqtt.MQTT_ERR_SUCCESS:
                    logging.info(f"Сообщение успешно отправлено: {text}")
                else:
                    logging.error(f"Ошибка отправки MQTT: {mqtt_result.rc}")

        except Exception as e:
            logging.error(f"Ошибка распознавания: {e}")

    def process_audio(self):
        buffer = []
        samples_per_buffer = int(RATE * BUFFER_SECONDS)

        while self.running:
            try:
                # Собираем аудио данные
                while len(buffer) < samples_per_buffer:
                    if not self.audio_queue.empty():
                        chunk = self.audio_queue.get()
                        buffer.extend(chunk.flatten())
                    else:
                        time.sleep(0.1)
                        continue

                # Преобразуем в numpy массив
                audio_data = np.array(buffer[:samples_per_buffer], dtype=np.float32)
                buffer = buffer[samples_per_buffer:]  # Очищаем использованные данные

                # Проверяем на тишину
                if not self.is_silence(audio_data):
                    self.transcribe_audio(audio_data)

            except Exception as e:
                logging.error(f"Ошибка обработки аудио: {e}")
                time.sleep(0.1)

    def list_audio_devices(self):
        devices = sd.query_devices()
        logging.info("\nДоступные аудиоустройства:")
        for i, device in enumerate(devices):
            logging.info(f"{i}: {device['name']} (max inputs: {device['max_input_channels']})")
        return devices

    def start(self):
        try:
            devices = self.list_audio_devices()

            # Выбираем устройство с максимальным количеством входных каналов
            device_index = max(range(len(devices)),
                               key=lambda i: devices[i]['max_input_channels'])

            logging.info(f"\nИспользуется устройство {device_index}: {devices[device_index]['name']}")

            # Создаем и запускаем поток обработки
            process_thread = threading.Thread(target=self.process_audio)
            process_thread.start()

            # Запускаем поток записи
            with sd.InputStream(device=device_index,
                                channels=CHANNELS,
                                samplerate=RATE,
                                blocksize=CHUNK,
                                callback=self.audio_callback):
                logging.info("Запись начата. Нажмите Ctrl+C для остановки.")
                while True:
                    time.sleep(0.1)

        except KeyboardInterrupt:
            logging.info("Завершение работы...")
        except Exception as e:
            logging.error(f"Ошибка: {e}")
        finally:
            self.running = False
            self.cleanup()

    def cleanup(self):
        self.running = False
        self.mqtt_client.loop_stop()
        self.mqtt_client.disconnect()
        logging.info("Ресурсы освобождены")


if __name__ == "__main__":
    processor = AudioProcessor()
    processor.start()
