"""Snapshot de status do sistema (conectividade de câmeras/WhatsApp/voz) —
extraído de `src/gui/bridge.py` (`GuiBridge._build_status`) para ser
reaproveitado também pelo servidor remoto (`src/server/remote_server.py`),
que precisa do mesmo dado sem depender do PyQt6."""
import os
from typing import List, Optional

from config.schemas import AppConfig
from src.notifications.whatsapp_client import EVOLUTION_API_KEY_ENV_VAR


def build_status(pipelines: list, app_config: AppConfig, voice_controllers: Optional[List] = None) -> dict:
    whatsapp = app_config.whatsapp
    return {
        "cameras": [
            {"name": p.name, "connected": bool(p.cam.grabbed), "fps": round(p.fps, 1)}
            for p in pipelines
        ],
        "whatsapp_configured": bool(
            whatsapp.resolve_endpoint() and whatsapp.resolve_instance() and os.environ.get(EVOLUTION_API_KEY_ENV_VAR)
        ),
        "voice_available": bool(voice_controllers),
    }
