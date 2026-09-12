"""Schemas Pydantic de configuração (config.json) e agenda de contatos (contacts.json)."""
from typing import Dict, List, Optional, Union

from pydantic import BaseModel, Field, model_validator


class CameraSourceConfig(BaseModel):
    """Uma fonte de câmera: webcam local (`index`), RTSP customizado (`rtsp_url`)
    ou RTSP Intelbras montado a partir de `ip`/credenciais. Wi-Fi local e remoto
    via Tailscale usam exatamente este mesmo schema — só o `ip` muda."""

    name: str
    index: Optional[int] = None
    ip: Optional[str] = None
    user: str = "admin"
    password: Optional[str] = None
    port: int = 554
    channel: int = 1
    subtype: int = 1
    rtsp_url: Optional[str] = None

    def resolve_src(self) -> Union[int, str]:
        if self.index is not None:
            return self.index
        if self.rtsp_url:
            return self.rtsp_url
        if self.ip:
            if not self.password:
                raise ValueError(f"Fonte '{self.name}': campo 'password' obrigatorio quando 'ip' e usado.")
            return (
                f"rtsp://{self.user}:{self.password}@{self.ip}:{self.port}"
                f"/cam/realmonitor?channel={self.channel}&subtype={self.subtype}"
            )
        raise ValueError(f"Fonte '{self.name}' invalida: informe 'index', 'rtsp_url' ou 'ip'.")


class EventRuleConfig(BaseModel):
    """Configuração de disparo de UM evento (ex: EVT-01) da matriz de eventos."""

    enabled: bool = True
    severity_override: Optional[str] = None
    notify_contact_ids: List[str] = Field(default_factory=list)


class WhatsAppConfig(BaseModel):
    device_id: str = "SMA-RES-DEFAULT"
    endpoint: Optional[str] = None
    token: Optional[str] = None
    max_retries: int = 4
    backoff_base_seconds: float = 2.0


class AppConfig(BaseModel):
    cameras: List[CameraSourceConfig] = Field(
        default_factory=lambda: [CameraSourceConfig(name="Webcam Local", index=0)]
    )
    events: Dict[str, EventRuleConfig] = Field(default_factory=dict)
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)

    @model_validator(mode="after")
    def _ensure_at_least_one_camera(self):
        if not self.cameras:
            raise ValueError("config.json precisa declarar ao menos uma camera em 'cameras'.")
        return self


class Contact(BaseModel):
    id: str
    name: str
    relationship: Optional[str] = None
    whatsapp_number: Optional[str] = None


class ContactsFile(BaseModel):
    contacts: List[Contact] = Field(default_factory=list)

    def find(self, contact_id: str) -> Optional[Contact]:
        return next((c for c in self.contacts if c.id == contact_id), None)
