"""Identificação de comandos de emergência por palavra-chave em texto já
transcrito (ver src/audio/transcriber.py). Não é NLU: é casamento de
padrões (regex) sobre o texto normalizado (minúsculas, sem acentos) —
suficiente para o objetivo declarado ("identificar chamadas de emergência"
por frases/palavras-chave), e muito mais robusto a erros de transcrição do
que tentar uma análise sintática completa.

Frases reconhecidas (e variações):
  - "me liga" / "liga pra mim" / "liga para mim"        -> CALL_ME
  - "socorro" / "ajuda" / "estou com problemas" / ...    -> HELP
  - "envia uma mensagem" / "manda mensagem [para <nome>]" -> SEND_MESSAGE
  - "chama o/a <nome>" / "liga pro/pra <nome>"            -> CALL_CONTACT
"""
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional


def normalize(text: str) -> str:
    """minúsculas + remove acentos — o texto que sai do ASR frequentemente
    varia nisso, e casar direto contra isso evita duplicar cada padrão."""
    text = text.lower().strip()
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


@dataclass
class VoiceIntent:
    intent_type: str  # "CALL_ME" | "HELP" | "SEND_MESSAGE" | "CALL_CONTACT"
    contact_name: Optional[str] = None
    matched_text: str = ""


_HELP_PATTERNS = [
    re.compile(p) for p in [
        r"\bsocorro\b",
        r"\bme\s+ajuda\b",
        r"\bpreciso\s+de\s+ajuda\b",
        r"\bestou\s+com\s+problemas?\b",
        r"\bestou\s+mal\b",
        r"\bnao\s+estou\s+bem\b",
        r"\bnao\s+consigo\s+me\s+levantar\b",
        r"\bcaí\b|\bcai\b",
    ]
]

_CALL_ME_PATTERNS = [
    re.compile(p) for p in [
        r"\bme\s+liga\b",
        r"\bliga\s+pra\s+mim\b",
        r"\bliga\s+para\s+mim\b",
        r"\bpode\s+me\s+ligar\b",
    ]
]

# Nome de contato: uma unica palavra (nomes proprios costumam ser um
# primeiro nome - capturar mais de uma palavra tende a engolir "ruido"
# do fim da frase, ex: "chama a ana por favor" -> "ana por favor").
# O artigo (o/a) so e consumido quando seguido de espaco, para nao comer
# a primeira letra de nomes como "Ana" (senao "pra ana" -> nome "na").
_CALL_CONTACT_PATTERN = re.compile(r"\bcham[ae]\s+(?:(?:o|a)\s+)?(?P<name>[a-z]+)\b")
_LIGA_PARA_CONTACT_PATTERN = re.compile(r"\bliga\s+(?:pro|pra|para)\s+(?:(?:o|a)\s+)?(?P<name>[a-z]+)\b")
_SEND_MESSAGE_PATTERN = re.compile(
    r"\b(?:envia|manda)r?\s+(?:uma\s+)?mensagem(?:\s+(?:pro|pra|para)\s+(?:(?:o|a)\s+)?(?P<name>[a-z]+))?\b"
)


class EmergencyIntentMatcher:
    """Sem estado entre chamadas — pode ser reaproveitado livremente."""

    def match(self, text: str) -> Optional[VoiceIntent]:
        normalized = normalize(text)
        if not normalized:
            return None

        m = _CALL_CONTACT_PATTERN.search(normalized)
        if m and m.group("name"):
            return VoiceIntent(intent_type="CALL_CONTACT", contact_name=m.group("name"), matched_text=text)

        # Checado antes de _LIGA_PARA_CONTACT_PATTERN: "liga pra mim" nao e
        # uma chamada para um contato chamado "mim".
        if any(p.search(normalized) for p in _CALL_ME_PATTERNS):
            return VoiceIntent(intent_type="CALL_ME", matched_text=text)

        m = _LIGA_PARA_CONTACT_PATTERN.search(normalized)
        if m and m.group("name"):
            return VoiceIntent(intent_type="CALL_CONTACT", contact_name=m.group("name"), matched_text=text)

        m = _SEND_MESSAGE_PATTERN.search(normalized)
        if m:
            name = m.group("name")
            return VoiceIntent(intent_type="SEND_MESSAGE", contact_name=name, matched_text=text)

        if any(p.search(normalized) for p in _HELP_PATTERNS):
            return VoiceIntent(intent_type="HELP", matched_text=text)

        return None
