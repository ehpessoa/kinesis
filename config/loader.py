"""Carregamento de config.json e contacts.json.

Ambos os arquivos reais ficam fora do versionamento (ver .gitignore) para não
expor credenciais de câmera/token do WhatsApp. Use config.example.json e
contacts.example.json como modelo. Caminhos alternativos podem ser indicados
via variáveis de ambiente KINESIS_CONFIG / KINESIS_CONTACTS.
"""
import json
import os

from .schemas import AppConfig, ContactsFile

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(_THIS_DIR, "config.json")
DEFAULT_CONTACTS_PATH = os.path.join(_THIS_DIR, "contacts.json")
CONFIG_ENV_VAR = "KINESIS_CONFIG"
CONTACTS_ENV_VAR = "KINESIS_CONTACTS"


def load_app_config() -> AppConfig:
    config_path = os.environ.get(CONFIG_ENV_VAR, DEFAULT_CONFIG_PATH)
    if not os.path.exists(config_path):
        return AppConfig()
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return AppConfig.model_validate(data)


def load_contacts() -> ContactsFile:
    contacts_path = os.environ.get(CONTACTS_ENV_VAR, DEFAULT_CONTACTS_PATH)
    if not os.path.exists(contacts_path):
        return ContactsFile()
    with open(contacts_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return ContactsFile.model_validate(data)


def save_app_config(config: AppConfig) -> None:
    """Persiste config.json (usado pela GUI ao alterar a matriz de eventos ou
    as credenciais do WhatsApp). Escreve sempre no mesmo caminho resolvido
    por load_app_config (respeitando KINESIS_CONFIG)."""
    config_path = os.environ.get(CONFIG_ENV_VAR, DEFAULT_CONFIG_PATH)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config.model_dump(mode="json"), f, ensure_ascii=False, indent=2)


def save_contacts(contacts: ContactsFile) -> None:
    """Persiste contacts.json (usado pela GUI ao adicionar/remover contatos)."""
    contacts_path = os.environ.get(CONTACTS_ENV_VAR, DEFAULT_CONTACTS_PATH)
    with open(contacts_path, "w", encoding="utf-8") as f:
        json.dump(contacts.model_dump(mode="json"), f, ensure_ascii=False, indent=2)
