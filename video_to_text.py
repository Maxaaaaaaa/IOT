import json
import cv2
from ultralytics import YOLO
import numpy as np
import time
from collections import deque
import logging
import paho.mqtt.client as mqtt


class VideoAnalyzer:
    def __init__(self, model_file='yolov8n.pt', min_score=0.5):
        self.detector = YOLO(model_file)
        self.min_score = min_score
        self.perf_metrics = deque(maxlen=30)

        # Сетевое подключение
        self.client = mqtt.Client()
        self.client.username_pw_set("mqtt_user", "mqtt_pass")
        self.client.tls_set()
        self.client.connect("jcddef63.ala.eu-central-1.emqxsl.com", 8883)
        self.client.loop_start()

        self.data_channel = "vision/objects"
        self.visual_styles = {}

        # Инициализация видео
        self.video = cv2.VideoCapture(0)
        self.video.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.video.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        self.last_update = 0
        self.update_freq = 2.0
        self.prev_data = {}

    def analyze_frame(self, frame):
        start = time.time()

        try:
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            predictions = self.detector(rgb_frame, stream=True)
            data = self.process_predictions(frame, predictions)

            rate = 1.0 / max(time.time() - start, 0.001)
            self.perf_metrics.append(rate)

            self.show_metrics(frame, data)
            return frame, np.mean(self.perf_metrics), data

        except Exception as e:
            logging.error(f"Analysis error: {e}")
            return frame, 0, {}

    def process_predictions(self, frame, predictions):
        data = {}

        for pred in predictions:
            boxes = pred.boxes
            for box in boxes:
                score = float(box.conf[0])
                if score < self.min_score:
                    continue

                coords = map(int, box.xyxy[0])
                x1, y1, x2, y2 = coords
                class_id = int(box.cls[0])
                label = pred.names[class_id]

                data[label] = data.get(label, 0) + 1

                style = self.get_style(class_id)
                self.draw_box(frame, x1, y1, x2, y2, label, score, style)

        self.send_data(data)
        return data

    def get_style(self, class_id):
        if class_id not in self.visual_styles:
            self.visual_styles[class_id] = tuple(np.random.randint(0, 255, 3).tolist())
        return self.visual_styles[class_id]

    def draw_box(self, frame, x1, y1, x2, y2, label, score, color):
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        text = f"{label}: {score:.2f}"
        (w, h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
        cv2.rectangle(frame, (x1, y1 - 20), (x1 + w, y1), color, -1)
        cv2.putText(frame, text, (x1, y1 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

    def show_metrics(self, frame, data):
        y = 30
        cv2.putText(frame, f"FPS: {np.mean(self.perf_metrics):.1f}",
                    (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

        for name, count in data.items():
            y += 30
            cv2.putText(frame, f"{name}: {count}",
                        (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

    def send_data(self, data):
        now = time.time()
        if now - self.last_update >= self.update_freq or data != self.prev_data:
            if data:
                try:
                    self.client.publish(
                        self.data_channel,
                        json.dumps({"objects": data}),
                        qos=1
                    )
                    self.last_update = now
                    self.prev_data = data.copy()
                except Exception as e:
                    logging.error(f"Data transmission error: {e}")

    def run(self):
        try:
            while True:
                success, frame = self.video.read()
                if not success:
                    logging.error("Video capture failed")
                    break

                frame, _, _ = self.analyze_frame(frame)
                cv2.imshow('Analysis', frame)

                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break

        finally:
            self.video.release()
            cv2.destroyAllWindows()
            self.client.loop_stop()
            self.client.disconnect()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    analyzer = VideoAnalyzer()
    analyzer.run()
