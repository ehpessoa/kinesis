import json

import pytest

from config.loader import load_app_config, load_contacts, save_app_config, save_contacts
from config.schemas import (
    AppConfig,
    CameraSourceConfig,
    CheckinConfig,
    Contact,
    ContactsFile,
    RemoteServerConfig,
)


def test_camera_source_resolve_src_index():
    cam = CameraSourceConfig(name="Webcam", index=0)
    assert cam.resolve_src() == 0


def test_camera_source_resolve_src_rtsp_url():
    cam = CameraSourceConfig(name="Custom", rtsp_url="rtsp://x:y@1.2.3.4/stream")
    assert cam.resolve_src() == "rtsp://x:y@1.2.3.4/stream"


def test_camera_source_resolve_src_ip_builds_rtsp_url():
    cam = CameraSourceConfig(name="Intelbras", ip="192.168.1.108", user="admin", password="SENHA")
    assert cam.resolve_src() == "rtsp://admin:SENHA@192.168.1.108:554/cam/realmonitor?channel=1&subtype=1"


def test_camera_source_resolve_src_ip_without_password_raises():
    cam = CameraSourceConfig(name="Sem senha", ip="192.168.1.108")
    with pytest.raises(ValueError, match="password"):
        cam.resolve_src()


def test_camera_source_resolve_src_invalid_raises():
    cam = CameraSourceConfig(name="Invalida")
    with pytest.raises(ValueError):
        cam.resolve_src()


def test_app_config_default_falls_back_to_local_webcam():
    config = AppConfig()
    assert len(config.cameras) == 1
    assert config.cameras[0].resolve_src() == 0


def test_app_config_rejects_empty_cameras_list():
    with pytest.raises(ValueError):
        AppConfig(cameras=[])


def test_load_app_config_without_file_returns_default(tmp_path, monkeypatch):
    monkeypatch.setenv("KINESIS_CONFIG", str(tmp_path / "does_not_exist.json"))
    config = load_app_config()
    assert config.cameras[0].resolve_src() == 0


def test_load_contacts_without_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("KINESIS_CONTACTS", str(tmp_path / "does_not_exist.json"))
    contacts = load_contacts()
    assert contacts.contacts == []


def test_config_example_json_validates_against_schema(repo_root):
    with open(f"{repo_root}/config/config.example.json", encoding="utf-8") as f:
        data = json.load(f)
    config = AppConfig.model_validate(data)
    assert len(config.cameras) == 3
    assert config.cameras[1].resolve_src().startswith("rtsp://admin:CHAVE_ACESSO_1@192.168.1.108")
    assert config.events["EVT-01"].enabled is True


def test_contacts_example_json_validates_against_schema(repo_root):
    with open(f"{repo_root}/config/contacts.example.json", encoding="utf-8") as f:
        data = json.load(f)
    contacts = ContactsFile.model_validate(data)
    assert contacts.find("filho_carlos").name == "Carlos"
    assert contacts.find("inexistente") is None


def test_save_and_load_app_config_roundtrip(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    monkeypatch.setenv("KINESIS_CONFIG", str(config_path))

    config = AppConfig(cameras=[CameraSourceConfig(name="Cam Teste", index=1)])
    save_app_config(config)
    assert config_path.exists()

    reloaded = load_app_config()
    assert reloaded.cameras[0].name == "Cam Teste"
    assert reloaded.cameras[0].index == 1


def test_checkin_config_defaults():
    config = AppConfig()
    assert config.checkins.enabled is False
    assert config.checkins.times == ["08:00", "14:00", "20:00"]
    assert config.checkins.notify_contact_ids == []


def test_checkin_config_rejects_invalid_hour():
    with pytest.raises(ValueError, match="Horario de check-in invalido"):
        CheckinConfig(times=["25:00"])


def test_checkin_config_rejects_malformed_time():
    with pytest.raises(ValueError, match="Horario de check-in invalido"):
        CheckinConfig(times=["horario_invalido"])


def test_remote_server_config_requires_token_when_enabled():
    with pytest.raises(ValueError, match="token"):
        RemoteServerConfig(enabled=True, token=None)


def test_remote_server_config_allows_disabled_without_token():
    config = RemoteServerConfig(enabled=False)
    assert config.token is None


def test_remote_server_config_accepts_token_when_enabled():
    config = RemoteServerConfig(enabled=True, token="segredo-longo")
    assert config.token == "segredo-longo"


def test_storage_config_defaults():
    config = AppConfig()
    assert config.storage.retention_hours == 24.0
    assert config.storage.purge_interval_minutes == 60.0


def test_save_and_load_contacts_roundtrip(tmp_path, monkeypatch):
    contacts_path = tmp_path / "contacts.json"
    monkeypatch.setenv("KINESIS_CONTACTS", str(contacts_path))

    contacts = ContactsFile(contacts=[Contact(id="abc", name="Fulano", whatsapp_number="5511900001111")])
    save_contacts(contacts)
    assert contacts_path.exists()

    reloaded = load_contacts()
    assert reloaded.find("abc").name == "Fulano"
