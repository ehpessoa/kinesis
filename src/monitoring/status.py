"""Snapshot de status do sistema (conectividade de câmeras/WhatsApp/voz) —
extraído de `src/gui/bridge.py` (`GuiBridge._build_status`) para ser
reaproveitado também pelo servidor remoto (`src/server/remote_server.py`),
que precisa do mesmo dado sem depender do PyQt6."""
from typing import List, Optional

from config.schemas import AppConfig


def build_status(pipelines: list, app_config: AppConfig, voice_controllers: Optional[List] = None) -> dict:
    return {
        "cameras": [
            {"name": p.name, "connected": bool(p.cam.grabbed), "fps": round(p.fps, 1)}
            for p in pipelines
        ],
        "whatsapp_configured": bool(app_config.whatsapp.endpoint),
        "voice_available": bool(voice_controllers),
    }
