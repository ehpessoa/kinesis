"""Núcleo do módulo de voz: identificação de comandos de emergência por
palavra-chave. Testado exaustivamente porque não depende de áudio nem de
modelo de ASR — é casamento de padrões sobre texto puro."""
import pytest

from src.audio.intent_matcher import EmergencyIntentMatcher, normalize


@pytest.fixture
def matcher():
    return EmergencyIntentMatcher()


@pytest.mark.parametrize("text", [
    "me liga por favor",
    "ME LIGA",
    "liga pra mim quando puder",
    "liga para mim",
    "pode me ligar",
])
def test_call_me_variations(matcher, text):
    intent = matcher.match(text)
    assert intent is not None
    assert intent.intent_type == "CALL_ME"
    assert intent.contact_name is None


@pytest.mark.parametrize("text", [
    "socorro",
    "me ajuda",
    "preciso de ajuda urgente",
    "estou com problemas",
    "estou com problema",
    "estou mal",
    "não estou bem",
    "nao estou bem",
    "não consigo me levantar",
])
def test_help_variations(matcher, text):
    intent = matcher.match(text)
    assert intent is not None
    assert intent.intent_type == "HELP"


@pytest.mark.parametrize("text,expected_name", [
    ("envia uma mensagem", None),
    ("envia mensagem", None),
    ("manda uma mensagem", None),
    ("manda mensagem para o carlos", "carlos"),
    ("envia mensagem pra ana", "ana"),
    ("manda mensagem pro joao", "joao"),
])
def test_send_message_variations(matcher, text, expected_name):
    intent = matcher.match(text)
    assert intent is not None
    assert intent.intent_type == "SEND_MESSAGE"
    assert intent.contact_name == expected_name


@pytest.mark.parametrize("text,expected_name", [
    ("chama o carlos", "carlos"),
    ("chama a ana", "ana"),
    ("chama a ana por favor", "ana"),
    ("chame o carlos", "carlos"),
    ("liga pro joao", "joao"),
    ("liga pra maria", "maria"),
])
def test_call_contact_variations(matcher, text, expected_name):
    intent = matcher.match(text)
    assert intent is not None
    assert intent.intent_type == "CALL_CONTACT"
    assert intent.contact_name == expected_name


@pytest.mark.parametrize("text", [
    "oi, como você está hoje",
    "",
    "bom dia",
    "vou preparar o almoço",
])
def test_no_match_for_unrelated_speech(matcher, text):
    assert matcher.match(text) is None


def test_call_me_takes_priority_over_call_contact_with_name_mim():
    """'liga pra mim' nao deveria ser interpretado como 'chamar um contato
    de nome mim' - e um bug real que apareceu na primeira versao do
    matcher (o artigo/pronome era capturado como nome de contato)."""
    matcher = EmergencyIntentMatcher()
    intent = matcher.match("liga pra mim quando puder")
    assert intent.intent_type == "CALL_ME"


def test_article_is_not_swallowed_into_contact_name():
    """Bug real encontrado nesta sessao: '(?:o|a)?' sem exigir espaco
    apos consumia a primeira letra de nomes como 'Ana' -> capturava 'na'."""
    matcher = EmergencyIntentMatcher()
    intent = matcher.match("envia mensagem pra ana")
    assert intent.contact_name == "ana"


def test_normalize_strips_accents_and_lowercases():
    assert normalize("NÃO ESTOU BEM") == "nao estou bem"
    assert normalize("Café com Açúcar") == "cafe com acucar"
