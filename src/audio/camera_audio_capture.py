"""Captura de áudio para o módulo de voz.

Câmeras RTSP normalmente carregam também uma faixa de áudio; extraímos
esse áudio via um subprocesso `ffmpeg` — independente da captura de vídeo
feita por `ThreadedCamera`/OpenCV, que só decodifica frames de imagem — e o
convertemos para PCM 16-bit mono a 16kHz. Essa extração real a partir de
uma câmera RTSP não pôde ser validada neste ambiente (sem hardware de
câmera com faixa de áudio disponível); o mesmo comando `ffmpeg` foi
validado extraindo PCM de um arquivo local de teste (ver tests/), o que
exercita a mesma lógica de decodificação/resample — só não o transporte
RTSP em si.

Para uma webcam local (fonte numérica), não há uma "faixa de áudio da
câmera" acessível via OpenCV — o microfone de um notebook ou webcam USB é
exposto pelo sistema operacional como um dispositivo de áudio separado.
Nesse caso, a captura usa o microfone padrão do sistema via `sounddevice`
como aproximação prática de "áudio da câmera".

Em qualquer um dos dois casos, a ausência do recurso (ffmpeg não
instalado, nenhum dispositivo de áudio disponível) desliga a captura
silenciosamente em vez de derrubar o processo — o módulo de voz é uma
funcionalidade adicional, uma câmera sem áudio disponível não deveria
impedir o restante do pipeline de vídeo.
"""
import queue
import subprocess
import threading
from typing import Optional, Union

import numpy as np

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2  # PCM s16le


class CameraAudioCapture:
    def __init__(self, source: Union[int, str], chunk_seconds: float = 4.0, sample_rate: int = SAMPLE_RATE):
        self.source = source
        self.chunk_seconds = chunk_seconds
        self.sample_rate = sample_rate
        self._chunk_bytes = int(chunk_seconds * sample_rate) * BYTES_PER_SAMPLE
        self._queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=8)
        self._buffer = bytearray()
        self._process: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def is_rtsp_or_file_source(self) -> bool:
        return isinstance(self.source, str)

    def start(self):
        if self._running:
            return self
        self._running = True
        target = self._run_ffmpeg if self.is_rtsp_or_file_source() else self._run_microphone
        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()
        return self

    def push_pcm_bytes(self, data: bytes):
        """Acumula bytes PCM s16le mono e enfileira chunks de tamanho fixo
        (`chunk_seconds`) como arrays float32 normalizados em [-1, 1].
        Exposto publicamente para permitir testar a lógica de janelamento
        com dados sintéticos, sem depender de ffmpeg/microfone reais."""
        self._buffer.extend(data)
        while len(self._buffer) >= self._chunk_bytes:
            chunk_bytes = bytes(self._buffer[:self._chunk_bytes])
            del self._buffer[:self._chunk_bytes]
            audio = np.frombuffer(chunk_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            try:
                self._queue.put_nowait(audio)
            except queue.Full:
                pass  # fila cheia: descarta o chunk mais novo, prioriza acompanhar tempo real

    def _run_ffmpeg(self):
        cmd = [
            "ffmpeg", "-loglevel", "error", "-i", str(self.source),
            "-vn", "-acodec", "pcm_s16le", "-ar", str(self.sample_rate), "-ac", "1",
            "-f", "s16le", "-",
        ]
        try:
            self._process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            return  # ffmpeg nao instalado - captura de audio desabilitada silenciosamente

        while self._running and self._process.poll() is None:
            data = self._process.stdout.read(4096)
            if not data:
                break
            self.push_pcm_bytes(data)

    def _run_microphone(self):
        try:
            import sounddevice as sd
        except ImportError:
            return

        def callback(indata, frames, time_info, status):
            self.push_pcm_bytes((indata[:, 0] * 32768.0).astype(np.int16).tobytes())

        try:
            with sd.InputStream(samplerate=self.sample_rate, channels=1, dtype="float32", callback=callback):
                while self._running:
                    sd.sleep(200)
        except Exception:
            return  # sem dispositivo de audio disponivel - captura desabilitada silenciosamente

    def get_chunk(self, timeout: float = 1.0) -> Optional[np.ndarray]:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self):
        self._running = False
        if self._process is not None:
            self._process.terminate()
