import json
from datetime import datetime, timezone

import httpx

from config.schemas import WhatsAppConfig
from src.behavior.event_engine import EventNotification
from src.notifications.whatsapp_client import WhatsAppNotifier


def make_event(**overrides):
    defaults = dict(
        event_id="EVT-01", category="Queda", name="Queda Brusca Detectada", severity="CRITICAL",
        timestamp=datetime.now(timezone.utc), source_name="Sala de Estar", message="Queda simulada.",
    )
    defaults.update(overrides)
    return EventNotification(**defaults)


def test_send_event_succeeds_after_retries():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        payload = json.loads(request.read())
        assert payload["event_type"] == "EVT-01"
        assert payload["device_id"] == "SMA-TESTE"
        assert request.headers.get("Authorization") == "Bearer TOKEN123"
        if calls["count"] < 3:
            return httpx.Response(500)
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    config = WhatsAppConfig(device_id="SMA-TESTE", endpoint="https://zap.example/send", token="TOKEN123",
                             max_retries=4, backoff_base_seconds=0.01)
    notifier = WhatsAppNotifier(config, client=client)

    ok = notifier.send_event(make_event(), recipient_number="5511999998888")

    assert ok is True
    assert calls["count"] == 3


def test_send_event_fails_after_exhausting_retries():
    calls = {"count": 0}

    def always_fail(request):
        calls["count"] += 1
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(always_fail))
    config = WhatsAppConfig(endpoint="https://zap.example/send", max_retries=3, backoff_base_seconds=0.01)
    notifier = WhatsAppNotifier(config, client=client)

    ok = notifier.send_event(make_event(), recipient_number="5511999998888")

    assert ok is False
    assert calls["count"] == 3


def test_send_event_without_endpoint_returns_false_without_network_call():
    config = WhatsAppConfig(endpoint=None)
    notifier = WhatsAppNotifier(config, client=httpx.Client())

    ok = notifier.send_event(make_event(), recipient_number="5511999998888")

    assert ok is False


def test_payload_includes_person_label_when_present():
    captured = {}

    def handler(request):
        captured["payload"] = json.loads(request.read())
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    config = WhatsAppConfig(endpoint="https://zap.example/send")
    notifier = WhatsAppNotifier(config, client=client)

    notifier.send_event(make_event(person_label="Pessoa 42"), recipient_number="5511999998888")

    assert "(Pessoa 42)" in captured["payload"]["message"]


def test_payload_includes_media_attachment_when_frame_provided():
    captured = {}

    def handler(request):
        captured["payload"] = json.loads(request.read())
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    config = WhatsAppConfig(endpoint="https://zap.example/send")
    notifier = WhatsAppNotifier(config, client=client)

    notifier.send_event(make_event(), recipient_number="5511999998888", frame_jpeg_base64="ZmFrZQ==")

    assert captured["payload"]["media_attachment"] == {"type": "image/jpeg", "base64_data": "ZmFrZQ=="}
