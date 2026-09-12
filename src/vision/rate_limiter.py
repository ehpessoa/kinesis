"""Limitador de taxa por tempo de parede — mitigação do item 4.1.1 do plano
("Processamento Multimodal Completo Simultâneo em Hardware Básico"): rodar
Pose, Face e Gesture a cada frame da câmera sobrecarrega CPU sem GPU
dedicada. O plano recomenda Pose a 15-20 FPS e Face/Blendshapes a 5-10 FPS.

Por que tempo de parede (segundos) e não contagem de frames: a taxa real de
captura varia entre webcam local (~30 FPS) e câmera RTSP (Wi-Fi ou remota
via VPN, sujeita a jitter de rede) — um throttle "a cada N frames" teria um
FPS efetivo diferente em cada fonte. Medir por tempo garante o mesmo ritmo
(Hz) de cada sub-pipeline independente da fonte.
"""
import time
from typing import Optional


class RateLimiter:
    def __init__(self, target_fps: float):
        self.target_fps = target_fps
        self.min_interval = (1.0 / target_fps) if target_fps > 0 else 0.0
        self._last_run_at = 0.0

    def should_run(self, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        if now - self._last_run_at >= self.min_interval:
            self._last_run_at = now
            return True
        return False
