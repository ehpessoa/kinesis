"""Utilitário compartilhado para o download de pesos de modelos YOLO
(ultralytics baixa relativo ao diretório de trabalho atual). Usado por
src/vision/object_detector.py e src/vision/person_tracker.py para garantir
que os pesos caiam em models/ (já gitignorada, mesma pasta dos modelos
MediaPipe) em vez de poluir a raiz do repositório.

Caminho customizável via KINESIS_MODELS_DIR (mesmo padrão de KINESIS_CONFIG/
KINESIS_EVENT_LOG em config/loader.py e src/storage/) — útil quando os pesos
devem ficar num disco/particao diferente do checkout do projeto."""
import contextlib
import os

MODELS_DIR_ENV_VAR = "KINESIS_MODELS_DIR"

_DEFAULT_MODELS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "models")
)
MODELS_DIR = os.environ.get(MODELS_DIR_ENV_VAR, _DEFAULT_MODELS_DIR)


@contextlib.contextmanager
def download_into_models_dir():
    os.makedirs(MODELS_DIR, exist_ok=True)
    prev_cwd = os.getcwd()
    os.chdir(MODELS_DIR)
    try:
        yield
    finally:
        os.chdir(prev_cwd)
