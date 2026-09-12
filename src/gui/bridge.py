"""Ponte QWebChannel entre o backend Python (captura/visão/eventos) e o
frontend HTML/JS local (src/gui/web/). Não há servidor HTTP: o
QWebEngineView carrega os arquivos estáticos via file:// e toda a
comunicação passa por este QObject, exposto ao JS como `bridge`.

Todos os métodos invocáveis pelo JS trocam strings JSON — é o formato mais
simples de marshaling estável entre PyQt6 e QWebChannel para estruturas
compostas (listas de contatos, matriz de eventos etc.).
"""
import json
import re
import time
from datetime import datetime, timezone
from typing import List

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from config.loader import save_app_config, save_contacts
from config.schemas import AppConfig, Contact, ContactsFile, EventRuleConfig
from src.audio.voice_controller import VOICE_EVENT_CATALOG
from src.behavior.event_engine import EVENT_CATALOG, EventNotification
from src.monitoring.status import build_status
from src.notifications.whatsapp_client import WhatsAppNotifier, encode_frame_jpeg_base64, encode_frame_jpeg_bytes

# Eventos EVT-05/12 nao rodam no EventEngine (ver README: exigem um
# classificador de olhar erratico e multiplas zonas de risco nomeadas que
# ainda nao existem). A GUI os lista mesmo assim, desabilitados, para nao
# esconder a lacuna da matriz completa. EVT-10/EVT-11 saíram desta lista
# nesta etapa - agora implementados no EventEngine (ver README), entram em
# IMPLEMENTED_EVENTS abaixo via EVENT_CATALOG.
UNIMPLEMENTED_EVENTS = {
    "EVT-05": {"category": "Atencao", "name": "Desorientacao / Confusao", "default_severity": "LOW"},
    "EVT-12": {"category": "Dispositivos", "name": "Entrada em Zona de Risco", "default_severity": "HIGH"},
}

# Eventos de visao (EVENT_CATALOG) + eventos de voz (VOICE_EVENT_CATALOG):
# ambos sao configuraveis pela mesma matriz `events` do config.json.
IMPLEMENTED_EVENTS = {**EVENT_CATALOG, **VOICE_EVENT_CATALOG}


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "contato"


class GuiBridge(QObject):
    frameReady = pyqtSignal(str, str)   # (source_name, jpeg_base64)
    eventLogged = pyqtSignal(str)       # JSON de EventNotification
    statusChanged = pyqtSignal(str)     # JSON {cameras, whatsapp_configured, voice_available}
    metricsUpdated = pyqtSignal(str)    # JSON {source_name, fps}

    def __init__(self, pipelines: list, app_config: AppConfig, contacts: ContactsFile,
                 notifier: WhatsAppNotifier, dispatcher, event_logger=None, voice_controllers=None,
                 checkin_scheduler=None, remote_server=None):
        super().__init__()
        self.pipelines = pipelines
        self.app_config = app_config
        self.contacts = contacts
        self.notifier = notifier
        self.dispatcher = dispatcher
        self.event_logger = event_logger
        self.voice_controllers = voice_controllers or []
        self.checkin_scheduler = checkin_scheduler
        self.remote_server = remote_server
        self.selected_index = 0
        self._last_status_emit = 0.0

    # --- laço de captura/análise, chamado por um QTimer em main_window.py ---
    def tick(self):
        now = time.time()
        for i, pipeline in enumerate(self.pipelines):
            result = pipeline.process_next_frame()
            if result is None:
                continue
            frame, metrics = result

            if i == self.selected_index:
                self.frameReady.emit(pipeline.name, encode_frame_jpeg_base64(frame))
                self.metricsUpdated.emit(json.dumps({"source_name": pipeline.name, "fps": round(pipeline.fps, 1)}))

            # Snapshot para o servidor remoto (todas as cameras, nao so a
            # selecionada na GUI local) - throttlado internamente pelo
            # RemoteStatusServer, um RateLimiter por camera.
            if self.remote_server is not None and self.remote_server.should_capture_snapshot(pipeline.name, now=now):
                self.remote_server.set_snapshot(pipeline.name, encode_frame_jpeg_bytes(frame))

            events = metrics["events"]
            if events:
                event_frame_b64 = encode_frame_jpeg_base64(frame)
                for event in events:
                    self.eventLogged.emit(event.model_dump_json())
                    if self.event_logger is not None:
                        self.event_logger.log(event)
                    self.dispatcher.dispatch(event, event_frame_b64)

        if now - self._last_status_emit > 1.0:
            self._last_status_emit = now
            self.statusChanged.emit(json.dumps(self._build_status()))

    def _build_status(self) -> dict:
        return build_status(self.pipelines, self.app_config, self.voice_controllers)

    def shutdown(self):
        for pipeline in self.pipelines:
            pipeline.close()
        for controller in self.voice_controllers:
            controller.stop()
        if self.checkin_scheduler is not None:
            self.checkin_scheduler.stop()
        if self.remote_server is not None:
            self.remote_server.stop()
        if self.event_logger is not None:
            self.event_logger.stop_auto_purge()
        self.notifier.close()

    # --- slots invocaveis pelo JS ---
    @pyqtSlot(result=str)
    def get_initial_state(self) -> str:
        events = {}
        for event_id, meta in IMPLEMENTED_EVENTS.items():
            rule = self.app_config.events.get(event_id, EventRuleConfig())
            events[event_id] = {
                "id": event_id, "category": meta["category"], "name": meta["name"],
                "severity": rule.severity_override or meta["default_severity"],
                "enabled": rule.enabled, "notify_contact_ids": rule.notify_contact_ids,
                "implemented": True,
            }
        for event_id, meta in UNIMPLEMENTED_EVENTS.items():
            events[event_id] = {
                "id": event_id, "category": meta["category"], "name": meta["name"],
                "severity": meta["default_severity"], "enabled": False,
                "notify_contact_ids": [], "implemented": False,
            }

        event_history = self.event_logger.read_recent(limit=50) if self.event_logger is not None else []

        return json.dumps({
            "cameras": [{"index": i, "name": p.name} for i, p in enumerate(self.pipelines)],
            "selected_index": self.selected_index,
            "events": events,
            "contacts": [c.model_dump() for c in self.contacts.contacts],
            "whatsapp": self.app_config.whatsapp.model_dump(),
            "status": self._build_status(),
            "event_history": event_history,
        })

    @pyqtSlot(int)
    def select_camera(self, index: int):
        if 0 <= index < len(self.pipelines):
            self.selected_index = index

    @pyqtSlot(str, bool)
    def set_event_enabled(self, event_id: str, enabled: bool):
        if event_id not in IMPLEMENTED_EVENTS:
            return  # evento ainda nao implementado: a GUI nao deveria habilitar
        rule = self.app_config.events.setdefault(event_id, EventRuleConfig())
        rule.enabled = enabled
        save_app_config(self.app_config)

    @pyqtSlot(str, str)
    def set_event_contacts(self, event_id: str, contact_ids_json: str):
        if event_id not in IMPLEMENTED_EVENTS:
            return
        rule = self.app_config.events.setdefault(event_id, EventRuleConfig())
        rule.notify_contact_ids = list(json.loads(contact_ids_json))
        save_app_config(self.app_config)

    @pyqtSlot(str, result=str)
    def add_contact(self, contact_json: str) -> str:
        try:
            data = json.loads(contact_json)
            base_id = _slugify(data.get("name", "contato"))
            existing_ids = {c.id for c in self.contacts.contacts}
            new_id, suffix = base_id, 2
            while new_id in existing_ids:
                new_id = f"{base_id}_{suffix}"
                suffix += 1
            contact = Contact(
                id=new_id, name=data["name"],
                relationship=data.get("relationship"),
                whatsapp_number=data.get("whatsapp_number"),
            )
            self.contacts.contacts.append(contact)
            save_contacts(self.contacts)
            return json.dumps({"ok": True, "contact": contact.model_dump()})
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)})

    @pyqtSlot(str, result=str)
    def delete_contact(self, contact_id: str) -> str:
        self.contacts.contacts = [c for c in self.contacts.contacts if c.id != contact_id]
        for rule in self.app_config.events.values():
            rule.notify_contact_ids = [c for c in rule.notify_contact_ids if c != contact_id]
        save_contacts(self.contacts)
        save_app_config(self.app_config)
        return json.dumps({"ok": True})

    @pyqtSlot(str, result=str)
    def save_whatsapp_config(self, whatsapp_json: str) -> str:
        try:
            data = json.loads(whatsapp_json)
            self.app_config.whatsapp.device_id = data.get("device_id", self.app_config.whatsapp.device_id)
            self.app_config.whatsapp.endpoint = data.get("endpoint") or None
            self.app_config.whatsapp.token = data.get("token") or None
            save_app_config(self.app_config)
            return json.dumps({"ok": True})
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)})

    @pyqtSlot(str)
    def test_alert(self, event_id: str):
        meta = IMPLEMENTED_EVENTS.get(event_id) or UNIMPLEMENTED_EVENTS.get(event_id)
        if meta is None:
            return
        source_name = self.pipelines[self.selected_index].name if self.pipelines else "Teste"
        event = EventNotification(
            event_id=event_id, category=meta["category"], name=meta["name"],
            severity=meta["default_severity"], timestamp=datetime.now(timezone.utc),
            source_name=source_name, message="Alerta de teste disparado manualmente pela GUI.",
        )
        self.eventLogged.emit(event.model_dump_json())
        if self.event_logger is not None:
            self.event_logger.log(event)
        self.dispatcher.dispatch(event, frame_b64=None)
