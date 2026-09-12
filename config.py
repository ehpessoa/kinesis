"""Carregamento das fontes de câmera (webcam local, RTSP Wi-Fi ou RTSP remoto via VPN).

As fontes ficam em um arquivo JSON externo (não versionado, ver .gitignore) para
evitar credenciais/IPs de câmera hardcoded no código-fonte. Use cameras.example.json
como modelo. Sem esse arquivo, o comportamento padrão é usar a webcam local (índice 0).
"""
import json
import os
from dataclasses import dataclass
from typing import List, Union

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(_THIS_DIR, "cameras.json")
CONFIG_ENV_VAR = "KINESIS_CAMERAS_CONFIG"


@dataclass
class CameraSource:
    name: str
    src: Union[int, str]


def build_rtsp_url(user, password, ip, port=554, channel=1, subtype=1) -> str:
    """URL RTSP padrão para câmeras Intelbras Mibo (e compatíveis Dahua/Intelbras).

    Vale tanto para Wi-Fi local (Cenário 2) quanto para acesso remoto via IP de
    sub-rede do Tailscale (Cenário 3): a VPN é transparente para a aplicação,
    apenas o `ip` muda entre um cenário e outro.
    """
    return f"rtsp://{user}:{password}@{ip}:{port}/cam/realmonitor?channel={channel}&subtype={subtype}"


def _source_from_entry(entry: dict) -> CameraSource:
    name = entry.get("name", "Camera")

    if "index" in entry:
        return CameraSource(name=name, src=int(entry["index"]))

    if "rtsp_url" in entry:
        return CameraSource(name=name, src=entry["rtsp_url"])

    if "ip" in entry:
        if "password" not in entry:
            raise ValueError(f"Fonte '{name}': campo 'password' obrigatorio quando 'ip' e usado.")
        src = build_rtsp_url(
            user=entry.get("user", "admin"),
            password=entry["password"],
            ip=entry["ip"],
            port=entry.get("port", 554),
            channel=entry.get("channel", 1),
            subtype=entry.get("subtype", 1),
        )
        return CameraSource(name=name, src=src)

    raise ValueError(f"Fonte de camera invalida (faltam 'index', 'rtsp_url' ou 'ip'): {entry}")


def load_camera_sources() -> List[CameraSource]:
    config_path = os.environ.get(CONFIG_ENV_VAR, DEFAULT_CONFIG_PATH)

    if not os.path.exists(config_path):
        return [CameraSource(name="Webcam Local", src=0)]

    with open(config_path, "r", encoding="utf-8") as f:
        entries = json.load(f)

    if not entries:
        return [CameraSource(name="Webcam Local", src=0)]

    return [_source_from_entry(entry) for entry in entries]
