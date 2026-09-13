"""Fixtures compartilhadas: isola cada teste de config/config.json,
config/contacts.json e do estado em data/ (event log + fila de
notificações) do repositório real, redirecionando para arquivos
temporários via as mesmas variáveis de ambiente que a aplicação usa
(KINESIS_CONFIG, KINESIS_CONTACTS, KINESIS_EVENT_LOG, KINESIS_PENDING_QUEUE).

Também remove do ambiente do teste as variáveis de segredo/override
(EVOLUTION_API_KEY, KINESIS_WHATSAPP_*, KINESIS_REMOTE_SERVER_TOKEN,
KINESIS_VOICE_MODEL_DIR, CAM_*_PASSWORD via password_env, KINESIS_MODELS_DIR)
para que um ".env" real presente na máquina de quem roda os testes não vaze
segredo nenhum para dentro da suíte nem mude o resultado de um teste que
não define essas variáveis explicitamente.
"""
import os

import pytest

_SECRET_ENV_VARS_TO_ISOLATE = (
    "EVOLUTION_API_KEY",
    "KINESIS_WHATSAPP_ENDPOINT",
    "KINESIS_WHATSAPP_INSTANCE",
    "KINESIS_REMOTE_SERVER_TOKEN",
    "KINESIS_VOICE_MODEL_DIR",
    "KINESIS_MODELS_DIR",
)


@pytest.fixture(autouse=True)
def isolated_storage_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("KINESIS_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("KINESIS_CONTACTS", str(tmp_path / "contacts.json"))
    monkeypatch.setenv("KINESIS_EVENT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("KINESIS_PENDING_QUEUE", str(tmp_path / "pending_notifications.json"))
    for env_var in _SECRET_ENV_VARS_TO_ISOLATE:
        monkeypatch.delenv(env_var, raising=False)
    yield


@pytest.fixture
def repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
