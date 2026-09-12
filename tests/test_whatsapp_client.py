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
        assert request.url.path == "/message/sendText/senszia"
        payload = json.loads(request.read())
        assert payload["number"] == "5511999998888"
        assert request.headers.get("apikey") == "APIKEY123"
        if calls["count"] < 3:
            return httpx.Response(500)
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    config = WhatsAppConfig(endpoint="https://message.senszia.com", instance="senszia",
                             max_retries=4, backoff_base_seconds=0.01)
    notifier = WhatsAppNotifier(config, client=client, api_key="APIKEY123")

    ok = notifier.send_event(make_event(), recipient_number="5511999998888")

    assert ok is True
    assert calls["count"] == 3


def test_send_event_fails_after_exhausting_retries():
    calls = {"count": 0}

    def always_fail(request):
        calls["count"] += 1
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(always_fail))
    config = WhatsAppConfig(endpoint="https://message.senszia.com", instance="senszia",
                             max_retries=3, backoff_base_seconds=0.01)
    notifier = WhatsAppNotifier(config, client=client, api_key="APIKEY123")

    ok = notifier.send_event(make_event(), recipient_number="5511999998888")

    assert ok is False
    assert calls["count"] == 3


def test_send_event_without_endpoint_returns_false_without_network_call():
    config = WhatsAppConfig(endpoint=None, instance="senszia")
    notifier = WhatsAppNotifier(config, client=httpx.Client(), api_key="APIKEY123")

    ok = notifier.send_event(make_event(), recipient_number="5511999998888")

    assert ok is False


def test_send_event_without_instance_returns_false_without_network_call():
    config = WhatsAppConfig(endpoint="https://message.senszia.com", instance=None)
    notifier = WhatsAppNotifier(config, client=httpx.Client(), api_key="APIKEY123")

    ok = notifier.send_event(make_event(), recipient_number="5511999998888")

    assert ok is False


def test_send_event_without_api_key_returns_false_without_network_call():
    config = WhatsAppConfig(endpoint="https://message.senszia.com", instance="senszia")
    notifier = WhatsAppNotifier(config, client=httpx.Client(), api_key=None)

    ok = notifier.send_event(make_event(), recipient_number="5511999998888")

    assert ok is False


def test_payload_includes_person_label_when_present():
    captured = {}

    def handler(request):
        captured["payload"] = json.loads(request.read())
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    config = WhatsAppConfig(endpoint="https://message.senszia.com", instance="senszia")
    notifier = WhatsAppNotifier(config, client=client, api_key="APIKEY123")

    notifier.send_event(make_event(person_label="Pessoa 42"), recipient_number="5511999998888")

    assert "(Pessoa 42)" in captured["payload"]["text"]


def test_recipient_number_is_normalized():
    captured = {}

    def handler(request):
        captured["payload"] = json.loads(request.read())
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    config = WhatsAppConfig(endpoint="https://message.senszia.com", instance="senszia")
    notifier = WhatsAppNotifier(config, client=client, api_key="APIKEY123")

    notifier.send_event(make_event(), recipient_number="+55 (11) 99999-8888")

    assert captured["payload"]["number"] == "5511999998888"
