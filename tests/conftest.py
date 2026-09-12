"""Fixtures compartilhadas: isola cada teste de config/config.json,
config/contacts.json e do estado em data/ (event log + fila de
notificações) do repositório real, redirecionando para arquivos
temporários via as mesmas variáveis de ambiente que a aplicação usa
(KINESIS_CONFIG, KINESIS_CONTACTS, KINESIS_EVENT_LOG, KINESIS_PENDING_QUEUE).
"""
import os

import pytest


@pytest.fixture(autouse=True)
def isolated_storage_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("KINESIS_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("KINESIS_CONTACTS", str(tmp_path / "contacts.json"))
    monkeypatch.setenv("KINESIS_EVENT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("KINESIS_PENDING_QUEUE", str(tmp_path / "pending_notifications.json"))
    yield


@pytest.fixture
def repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
