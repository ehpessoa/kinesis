"""Utilitário compartilhado para o download de pesos de modelos YOLO
(ultralytics baixa relativo ao diretório de trabalho atual). Usado por
src/vision/object_detector.py e src/vision/person_tracker.py para garantir
que os pesos caiam em models/ (já gitignorada, mesma pasta dos modelos
MediaPipe) em vez de poluir a raiz do repositório."""
import contextlib
import os

MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "models")
MODELS_DIR = os.path.normpath(MODELS_DIR)


@contextlib.contextmanager
def download_into_models_dir():
    os.makedirs(MODELS_DIR, exist_ok=True)
    prev_cwd = os.getcwd()
    os.chdir(MODELS_DIR)
    try:
        yield
    finally:
        os.chdir(prev_cwd)
