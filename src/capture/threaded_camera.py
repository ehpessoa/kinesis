"""Captura de vídeo concorrente com reconexão automática por fonte."""
import threading
import time

import cv2


class ThreadedCamera:
    """Captura de vídeo em thread separada, com reconexão automática e buffer
    sempre atualizado (evita lag ao ler o frame mais recente em vez de enfileirar).

    Mantém um contador de frames próprio, usado como timestamp monotônico local
    ao alimentar detectores MediaPipe em modo VIDEO — cada fonte deve ter seus
    próprios detectores e sua própria linha de tempo (ver tracking.py/app_vision.py).
    """

    def __init__(self, src, name="Cam", reconnect_delay: float = 0.2):
        self.src = src
        self.name = name
        self.reconnect_delay = reconnect_delay
        self.cap = cv2.VideoCapture(self.src)
        self.grabbed, self.frame = self.cap.read()
        self.frame_count = 0
        self.started = False
        self.read_lock = threading.Lock()
        self.thread = None

    def is_local_webcam(self) -> bool:
        return isinstance(self.src, int)

    def start(self):
        if self.started:
            return self
        self.started = True
        self.thread = threading.Thread(target=self._update, daemon=True)
        self.thread.start()
        return self

    def _update(self):
        while self.started:
            grabbed, frame = self.cap.read()
            if not grabbed:
                time.sleep(self.reconnect_delay)
                if isinstance(self.src, str):
                    self.cap.release()
                    self.cap = cv2.VideoCapture(self.src)
                continue
            with self.read_lock:
                self.grabbed = grabbed
                self.frame = frame
                self.frame_count += 1

    def read(self):
        """Retorna (grabbed, frame, frame_count). frame_count permite detectar,
        no chamador, se um frame novo de fato chegou desde a última leitura."""
        with self.read_lock:
            if self.frame is not None:
                return self.grabbed, self.frame.copy(), self.frame_count
        return False, None, self.frame_count

    def stop(self):
        self.started = False
        if self.thread is not None:
            self.thread.join(timeout=1.0)
        if self.cap.isOpened():
            self.cap.release()
