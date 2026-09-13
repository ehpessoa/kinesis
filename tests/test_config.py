import json

import pytest

from config.loader import load_app_config, load_contacts, save_app_config, save_contacts
from config.schemas import (
    AppConfig,
    CameraSourceConfig,
    CheckinConfig,
    Contact,
    ContactsFile,
    NightRoutineConfig,
    PersonTrackingConfig,
    RemoteServerConfig,
    SeizureDetectionConfig,
    VoiceConfig,
    WhatsAppConfig,
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


def test_camera_source_resolve_src_uses_password_env(monkeypatch):
    monkeypatch.setenv("CAM_TEST_PASSWORD", "SENHA_DO_AMBIENTE")
    cam = CameraSourceConfig(name="Intelbras", ip="192.168.1.108", password_env="CAM_TEST_PASSWORD")
    assert cam.resolve_src() == "rtsp://admin:SENHA_DO_AMBIENTE@192.168.1.108:554/cam/realmonitor?channel=1&subtype=1"


def test_camera_source_resolve_src_password_env_takes_priority_over_password(monkeypatch):
    monkeypatch.setenv("CAM_TEST_PASSWORD", "SENHA_DO_AMBIENTE")
    cam = CameraSourceConfig(
        name="Intelbras", ip="192.168.1.108", password="SENHA_NO_JSON", password_env="CAM_TEST_PASSWORD",
    )
    assert "SENHA_DO_AMBIENTE" in cam.resolve_src()


def test_camera_source_resolve_src_password_env_missing_raises_with_var_name():
    cam = CameraSourceConfig(name="Intelbras", ip="192.168.1.108", password_env="CAM_TEST_PASSWORD")
    with pytest.raises(ValueError, match="CAM_TEST_PASSWORD"):
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


def test_config_example_json_validates_against_schema(repo_root, monkeypatch):
    monkeypatch.setenv("CAM_WIFI_LOCAL_PASSWORD", "CHAVE_ACESSO_1")
    monkeypatch.setenv("CAM_REMOTA_TAILSCALE_PASSWORD", "CHAVE_ACESSO_2")
    with open(f"{repo_root}/config/config.example.json", encoding="utf-8") as f:
        data = json.load(f)
    config = AppConfig.model_validate(data)
    assert len(config.cameras) == 3
    assert config.cameras[1].password_env == "CAM_WIFI_LOCAL_PASSWORD"
    assert config.cameras[1].resolve_src().startswith("rtsp://admin:CHAVE_ACESSO_1@192.168.1.108")
    assert config.events["EVT-01"].enabled is True


def test_config_example_json_camera_password_env_missing_raises(repo_root):
    with open(f"{repo_root}/config/config.example.json", encoding="utf-8") as f:
        data = json.load(f)
    config = AppConfig.model_validate(data)
    with pytest.raises(ValueError, match="CAM_WIFI_LOCAL_PASSWORD"):
        config.cameras[1].resolve_src()


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
    with pytest.raises(ValueError, match="Horario invalido"):
        CheckinConfig(times=["25:00"])


def test_checkin_config_rejects_malformed_time():
    with pytest.raises(ValueError, match="Horario invalido"):
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


def test_remote_server_config_accepts_token_only_from_env_var(monkeypatch):
    monkeypatch.setenv("KINESIS_REMOTE_SERVER_TOKEN", "segredo-do-ambiente")
    config = RemoteServerConfig(enabled=True, token=None)
    assert config.resolve_token() == "segredo-do-ambiente"


def test_remote_server_config_env_var_takes_priority_over_field(monkeypatch):
    monkeypatch.setenv("KINESIS_REMOTE_SERVER_TOKEN", "segredo-do-ambiente")
    config = RemoteServerConfig(enabled=True, token="segredo-no-json")
    assert config.resolve_token() == "segredo-do-ambiente"


def test_remote_server_config_falls_back_to_field_without_env_var():
    config = RemoteServerConfig(enabled=True, token="segredo-no-json")
    assert config.resolve_token() == "segredo-no-json"


def test_storage_config_defaults():
    config = AppConfig()
    assert config.storage.retention_hours == 24.0
    assert config.storage.purge_interval_minutes == 60.0


def test_night_routine_config_defaults_disabled():
    config = AppConfig()
    assert config.night_routine.enabled is False
    assert config.night_routine.bed_zone == []


def test_night_routine_config_requires_bed_zone_when_enabled():
    with pytest.raises(ValueError, match="bed_zone"):
        NightRoutineConfig(enabled=True, bed_zone=[(0.0, 0.0), (1.0, 0.0)])  # so 2 pontos


def test_night_routine_config_accepts_polygon_when_enabled():
    config = NightRoutineConfig(enabled=True, bed_zone=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])
    assert len(config.bed_zone) == 3


def test_night_routine_config_rejects_invalid_time_format():
    with pytest.raises(ValueError, match="Horario invalido"):
        NightRoutineConfig(night_start="25:00")


def test_app_config_rejects_night_routine_without_person_tracking():
    with pytest.raises(ValueError, match="person_tracking"):
        AppConfig(
            night_routine=NightRoutineConfig(enabled=True, bed_zone=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]),
            person_tracking=PersonTrackingConfig(enabled=False),
        )


def test_app_config_allows_night_routine_with_person_tracking():
    config = AppConfig(
        night_routine=NightRoutineConfig(enabled=True, bed_zone=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]),
        person_tracking=PersonTrackingConfig(enabled=True),
    )
    assert config.night_routine.enabled is True


def test_seizure_detection_config_defaults_disabled():
    config = AppConfig()
    assert config.seizure_detection.enabled is False
    assert config.seizure_detection.freq_min_hz == 2.0
    assert config.seizure_detection.freq_max_hz == 6.0


def test_seizure_detection_config_accepts_custom_thresholds():
    config = SeizureDetectionConfig(enabled=True, freq_min_hz=1.5, freq_max_hz=7.0, min_amplitude=0.1)
    assert config.freq_max_hz == 7.0


def test_save_and_load_contacts_roundtrip(tmp_path, monkeypatch):
    contacts_path = tmp_path / "contacts.json"
    monkeypatch.setenv("KINESIS_CONTACTS", str(contacts_path))

    contacts = ContactsFile(contacts=[Contact(id="abc", name="Fulano", whatsapp_number="5511900001111")])
    save_contacts(contacts)
    assert contacts_path.exists()

    reloaded = load_contacts()
    assert reloaded.find("abc").name == "Fulano"


def test_whatsapp_config_resolve_falls_back_to_fields_without_env_vars():
    config = WhatsAppConfig(endpoint="https://message.senszia.com", instance="senszia")
    assert config.resolve_endpoint() == "https://message.senszia.com"
    assert config.resolve_instance() == "senszia"


def test_whatsapp_config_resolve_prefers_env_vars(monkeypatch):
    monkeypatch.setenv("KINESIS_WHATSAPP_ENDPOINT", "https://outro-endpoint.example.com")
    monkeypatch.setenv("KINESIS_WHATSAPP_INSTANCE", "outra-instancia")
    config = WhatsAppConfig(endpoint="https://message.senszia.com", instance="senszia")
    assert config.resolve_endpoint() == "https://outro-endpoint.example.com"
    assert config.resolve_instance() == "outra-instancia"


def test_voice_config_resolve_model_dir_falls_back_to_field():
    config = VoiceConfig(model_dir="/caminho/no/json")
    assert config.resolve_model_dir() == "/caminho/no/json"


def test_voice_config_resolve_model_dir_prefers_env_var(monkeypatch):
    monkeypatch.setenv("KINESIS_VOICE_MODEL_DIR", "/caminho/do/ambiente")
    config = VoiceConfig(model_dir="/caminho/no/json")
    assert config.resolve_model_dir() == "/caminho/do/ambiente"
