"""Testes do aviso por WhatsApp de que o servidor remoto subiu
(src/monitoring/remote_server_notice.py). Usa um dispatcher falso (sem
WhatsApp/rede real), igual a test_checkin.py, e monkeypatch de
`_detect_outbound_ip` para não depender de rede real disponível no ambiente
de teste."""
from datetime import datetime

import pytest

from config.schemas import AppConfig, Contact, ContactsFile, RemoteServerConfig
from src.monitoring import remote_server_notice
from src.monitoring.remote_server_notice import (
    build_remote_server_url,
    notify_remote_server_started,
    resolve_advertised_host,
)


class FakeDispatcher:
    def __init__(self, accept: bool = True):
        self.calls = []
        self._accept = accept

    def dispatch_to_contact(self, event, contact_id, frame_b64=None):
        self.calls.append((event.event_id, contact_id, event.message))
        return self._accept


def make_config(**overrides):
    defaults = dict(enabled=True, host="100.101.102.103", port=8765, token="segredo-longo")
    defaults.update(overrides)
    return AppConfig(remote_server=RemoteServerConfig(**defaults))


def make_contacts():
    return ContactsFile(contacts=[
        Contact(id="filho_carlos", name="Carlos", whatsapp_number="5511999998888"),
        Contact(id="cuidadora_ana", name="Ana", whatsapp_number="5511977776666"),
    ])


def test_resolve_advertised_host_returns_explicit_host_as_is():
    assert resolve_advertised_host("100.101.102.103") == "100.101.102.103"


def test_resolve_advertised_host_returns_none_for_loopback():
    assert resolve_advertised_host("127.0.0.1") is None
    assert resolve_advertised_host("localhost") is None


def test_resolve_advertised_host_auto_detects_for_bind_all(monkeypatch):
    monkeypatch.setattr(remote_server_notice, "_detect_outbound_ip", lambda: "192.168.1.50")
    assert resolve_advertised_host("0.0.0.0") == "192.168.1.50"


def test_resolve_advertised_host_returns_none_when_detection_fails(monkeypatch):
    monkeypatch.setattr(remote_server_notice, "_detect_outbound_ip", lambda: None)
    assert resolve_advertised_host("0.0.0.0") is None


def test_build_remote_server_url_includes_token():
    url = build_remote_server_url("100.101.102.103", 8765, "segredo-longo")
    assert url == "http://100.101.102.103:8765/?token=segredo-longo"


def test_build_remote_server_url_without_token_omits_query_string():
    url = build_remote_server_url("100.101.102.103", 8765, None)
    assert url == "http://100.101.102.103:8765/"


def test_notify_sends_to_each_configured_contact():
    config = make_config(notify_contact_ids=["filho_carlos", "cuidadora_ana"])
    dispatcher = FakeDispatcher()

    sent = notify_remote_server_started(config, make_contacts(), dispatcher, now=datetime(2026, 9, 13, 14, 30))

    assert sent is True
    assert [call[0] for call in dispatcher.calls] == [
        remote_server_notice.REMOTE_SERVER_STARTED_EVENT_ID,
        remote_server_notice.REMOTE_SERVER_STARTED_EVENT_ID,
    ]
    assert [call[1] for call in dispatcher.calls] == ["filho_carlos", "cuidadora_ana"]


def test_notify_message_includes_date_time_and_url():
    config = make_config(notify_contact_ids=["filho_carlos"])
    dispatcher = FakeDispatcher()

    notify_remote_server_started(config, make_contacts(), dispatcher, now=datetime(2026, 9, 13, 14, 30))

    message = dispatcher.calls[0][2]
    assert "13/09/2026 14:30" in message
    assert "http://100.101.102.103:8765/?token=segredo-longo" in message


def test_notify_does_nothing_without_configured_contacts():
    config = make_config(notify_contact_ids=[])
    dispatcher = FakeDispatcher()

    sent = notify_remote_server_started(config, make_contacts(), dispatcher)

    assert sent is False
    assert dispatcher.calls == []


def test_notify_does_nothing_when_host_is_loopback():
    config = make_config(host="127.0.0.1", enabled=False, notify_contact_ids=["filho_carlos"])
    dispatcher = FakeDispatcher()

    sent = notify_remote_server_started(config, make_contacts(), dispatcher)

    assert sent is False
    assert dispatcher.calls == []


def test_notify_auto_detects_host_when_bind_all(monkeypatch):
    monkeypatch.setattr(remote_server_notice, "_detect_outbound_ip", lambda: "192.168.1.50")
    config = make_config(host="0.0.0.0", notify_contact_ids=["filho_carlos"])
    dispatcher = FakeDispatcher()

    notify_remote_server_started(config, make_contacts(), dispatcher, now=datetime(2026, 9, 13, 14, 30))

    assert "192.168.1.50" in dispatcher.calls[0][2]


def test_notify_returns_false_when_dispatcher_rejects_every_contact():
    config = make_config(notify_contact_ids=["filho_carlos"])
    dispatcher = FakeDispatcher(accept=False)

    sent = notify_remote_server_started(config, make_contacts(), dispatcher)

    assert sent is False
    assert len(dispatcher.calls) == 1


def test_notify_logs_event_when_event_logger_provided(tmp_path):
    from src.storage.event_log import EventLogger

    config = make_config(notify_contact_ids=["filho_carlos"])
    dispatcher = FakeDispatcher()
    logger = EventLogger(path=str(tmp_path / "events.db"))

    notify_remote_server_started(config, make_contacts(), dispatcher, event_logger=logger)

    logged = logger.read_all()
    assert len(logged) == 1
    assert logged[0]["event_id"] == remote_server_notice.REMOTE_SERVER_STARTED_EVENT_ID
    assert logged[0]["severity"] == "INFO"
