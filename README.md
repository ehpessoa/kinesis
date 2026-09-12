# 👁️ Kinesis — SMA-TR (Sistema de Monitoramento Assistencial de Idosos em Tempo Real)

O **Kinesis / SMA-TR** é uma solução de monitoramento comportamental e assistencial em tempo real, evoluindo de uma PoC de visão computacional para uma arquitetura modular Edge/Local-First: captura multi-fonte (webcam, RTSP local, RTSP remoto via VPN), análise multimodal com MediaPipe, um motor de eventos com severidade e disparo de alertas via WhatsApp — tudo processado localmente, sem enviar vídeo para serviços externos.

---

## 🏗️ Arquitetura Atual

```
config/            -> config.json / contacts.json (não versionados) + schemas Pydantic
src/capture/       -> ThreadedCamera: captura em thread própria, com reconexão automática
src/vision/        -> detectores MediaPipe + MobilityAidDetector (YOLO-World) + PersonTracker (ByteTrack) + RateLimiter
src/behavior/      -> BehaviorTracker (cinemática/expressões) + EventEngine (matriz de eventos)
src/audio/         -> CameraAudioCapture + SpeechTranscriber + EmergencyIntentMatcher + VoiceController
src/notifications/ -> WhatsAppNotifier (HTTPS + retry com backoff exponencial)
src/storage/       -> EventLogger (JSONL) + PendingNotificationQueue (fila durável de WhatsApp)
src/gui/           -> GuiBridge (QWebChannel) + MainWindow (PyQt6) + web/ (HTML/CSS/JS)
main.py            -> orquestra N CameraPipeline (1 por câmera) via janelas cv2.imshow
gui_main.py        -> mesma orquestração, apresentada como dashboard PyQt6/HTML
tests/             -> suíte pytest (config, EventEngine, WhatsApp, storage, pipeline, voz)
```

Para cada fonte de câmera configurada, o `main.py` cria um `CameraPipeline` **independente**, com:

1. **Captura** própria (`ThreadedCamera`), com reconexão automática e sem lag de rede.
2. **Detectores MediaPipe próprios** (`PoseLandmarker`, `FaceLandmarker`, `GestureRecognizer`), em modo `VIDEO`, alimentados com um **timestamp monotônico local** (contador de frames da própria fonte).
3. **`BehaviorTracker` e `EventEngine` próprios**, mantendo estado isolado por câmera/pessoa (histórico de quadris, timers de debounce de cada evento).

> ⚠️ **Por que cada fonte tem seus próprios detectores?** O modo `VIDEO` do MediaPipe mantém estado temporal interno (suavização/tracking) assumindo um fluxo contínuo de uma única cena. Compartilhar um único detector entre câmeras diferentes corromperia esse estado — por isso a arquitetura usa uma instância isolada por fonte.

Os modelos (`.task`) são baixados automaticamente na primeira execução para `models/` (não versionada).

---

## 📷 Cenários de Câmera Suportados

Do ponto de vista da aplicação, câmera **Wi-Fi local** e câmera **remota via VPN Tailscale (5G/4G)** são a mesma fonte RTSP genérica — a diferença está inteiramente na camada de rede, não no código.

```bash
cp config/config.example.json config/config.json
cp config/contacts.example.json config/contacts.json
```

Edite `config/config.json` → `cameras`:
* `index`: webcam local.
* `ip` + `user`/`password` (+ `port`, `channel`, `subtype`): monta a URL RTSP padrão Intelbras/Dahua.
* `rtsp_url`: URL RTSP completa customizada.

Nenhum dos dois arquivos é versionado (estão no `.gitignore`) para não expor credenciais/token no repositório. Caminhos alternativos podem ser indicados via `KINESIS_CONFIG` / `KINESIS_CONTACTS`. Sem `config.json`, o sistema usa a webcam local (índice 0) por padrão.

---

## 🚨 Matriz de Eventos (Event Engine)

| Código | Categoria | Evento | Severidade | Status |
| :--- | :--- | :--- | :--- | :--- |
| EVT-01 | Queda | Queda Brusca Detectada | Crítica | ✅ Implementado |
| EVT-02 | Saúde | Imobilidade Prolongada pós-queda | Crítica | ✅ Implementado |
| EVT-03 | Saúde | Inatividade Geral Excessiva | Média | ✅ Implementado |
| EVT-04 | Atenção | Sonolência / Olhos Fechados | Média | ✅ Implementado |
| EVT-05 | Atenção | Desorientação / Confusão | Baixa | ⛔ Não implementado |
| EVT-06 | Gestos | Gesto de SOS / Pedido de Ajuda | Alta | ✅ Implementado |
| EVT-07 | Gestos | Mão no Rosto / Mal-Estar | Média | ✅ Implementado |
| EVT-08 | Postura | Mudança de Postura | Informativa | ✅ Implementado |
| EVT-09 | Emoção | Expressão de Dor / Distress | Média | ✅ Implementado |
| EVT-10 | Rotina | Ausência da Cama no Horário Noturno | Alta | ⛔ Não implementado |
| EVT-11 | Saúde | Detecção de Convulsão / Tremores | Crítica | ⛔ Não implementado |
| EVT-12 | Dispositivos | Entrada em Zona de Risco | Alta | ⛔ Não implementado |

**Por que 4 eventos ficaram fora desta etapa** (`src/behavior/event_engine.py` documenta cada um): EVT-05 exige um classificador de padrão de olhar/cabeça errático sustentado; EVT-10 e EVT-12 exigem zonas espaciais configuráveis no frame (cama, escada, fogão) que ainda não existem; EVT-11 exige análise em frequência (FFT/zero-crossing) da oscilação de pulsos, um algoritmo novo e não uma extensão do que já existe. Implementá-los agora seria entregar detecção não confiável — preferimos deixá-los explícitos como próxima etapa.

Cada evento habilitado em `config.json → events` dispara uma notificação (`WhatsAppNotifier`) para os `notify_contact_ids` configurados, resolvidos em `contacts.json`.

---

## 📲 Integração WhatsApp

`src/notifications/whatsapp_client.py` envia `POST` HTTPS para o `endpoint` configurado em `config.json → whatsapp`, com `Authorization: Bearer <token>`, fila de retry com **backoff exponencial** (`max_retries` / `backoff_base_seconds`) e o payload:

```json
{
  "device_id": "SMA-RES-01042",
  "recipient_number": "5511999998888",
  "event_type": "EVT-01",
  "severity": "CRITICAL",
  "timestamp": "2026-09-12T20:40:00Z",
  "message": "[ALERTA SMA-TR] Queda Brusca Detectada: Queda brusca detectada.",
  "media_attachment": { "type": "image/jpeg", "base64_data": "..." },
  "voice_command_metadata": { "initiated_by_user": false, "transcribed_text": null }
}
```

O `endpoint`/`token` **não têm valor real de fábrica** — são placeholders em `config.example.json` a serem substituídos pelo gateway WhatsApp real do usuário.

---

## 🎙️ Módulo de Voz: Comandos de Emergência

`src/audio/` identifica comandos de emergência ditos em voz alta a partir do áudio da câmera (ou do microfone local, para webcam) e dispara a mesma notificação por WhatsApp usada pelos eventos de visão — **sem** o pipeline de diálogo bidirecional completo do plano original (Silero VAD + confirmação falada + TTS): aqui a entrada é reconhecimento de palavra-chave sobre o texto transcrito, que é o que este pedido específico precisa.

**Frases reconhecidas** (`src/audio/intent_matcher.py`, testado com 30+ variações):

| Dito | Intenção | Ação |
| :--- | :--- | :--- |
| "me liga", "liga pra mim" | `CALL_ME` | Notifica os contatos configurados em `VOZ-CHAME-ME` |
| "socorro", "estou com problemas", "preciso de ajuda", "não estou bem"... | `HELP` | Notifica os contatos configurados em `VOZ-SOCORRO` |
| "envia uma mensagem" [para \<nome\>] | `SEND_MESSAGE` | Com nome resolvido: direto ao contato. Sem nome (ou não resolvido): notifica `VOZ-MENSAGEM` |
| "chama o/a \<nome\>", "liga pro/pra \<nome\>" | `CALL_CONTACT` | Direto ao contato cujo nome bate com o dito. Nome não reconhecido: **nada é enviado** (evita notificar a pessoa errada) |

### Arquitetura

1. **`camera_audio_capture.py` (`CameraAudioCapture`):** para uma câmera RTSP, extrai a faixa de áudio via um subprocesso `ffmpeg` (`-vn -acodec pcm_s16le -ar 16000 -ac 1`) — independente da captura de vídeo do OpenCV, que só decodifica frames de imagem. Para webcam local (fonte numérica), não existe "áudio da câmera" acessível via OpenCV — o microfone do notebook/webcam USB é exposto pelo sistema como um dispositivo separado, então a captura usa o microfone padrão via `sounddevice` como aproximação prática. Em ambos os casos, a ausência do recurso (ffmpeg não instalado, nenhum dispositivo de áudio) desliga a captura silenciosamente, sem derrubar o resto do pipeline.
2. **`transcriber.py` (`SpeechTranscriber`):** ASR via `faster-whisper`, carregado de forma tardia (só na primeira transcrição real).
3. **`intent_matcher.py` (`EmergencyIntentMatcher`):** casamento de padrões (regex) sobre o texto normalizado (minúsculas, sem acentos) — não é NLU, é exatamente o "identificar por palavras-chave" pedido.
4. **`voice_controller.py` (`VoiceController`):** orquestra captura → transcrição → intenção → despacho, numa thread própria por câmera, desacoplada do laço de vídeo. Usa `NotificationDispatcher.dispatch_to_contact()` (novo método) para os casos `CALL_CONTACT`/`SEND_MESSAGE`-com-nome, que devem ir a um destinatário específico e não à lista `notify_contact_ids` configurada do evento.

**Cada câmera tem seu próprio `SpeechTranscriber`** (não compartilhado): modelos de ASR não são seguros para chamadas concorrentes vindas de threads diferentes, e cada `VoiceController` roda na sua própria thread — ao contrário do `MobilityAidDetector`, sempre chamado sequencialmente pelo laço de vídeo de uma única thread.

### ⚠️ Bloqueio de rede real encontrado nesta sessão

O download automático de modelo do `faster-whisper` usa o **Hugging Face Hub** (`huggingface.co`). No ambiente usado para construir este projeto, esse host retornou **403 (bloqueio de política de rede)** — confirmado também para `openaipublic.azureedge.net` (Whisper original da OpenAI) e `alphacephei.com` (modelos Vosk). As três fontes de modelo de ASR mais comuns estavam bloqueadas; não foi possível, portanto, **validar a transcrição de fala com um modelo real** neste ambiente.

O que **foi** validado de ponta a ponta com dados reais:
* Extração de áudio via `ffmpeg` (mesmo comando usado para câmeras RTSP), contra um arquivo de voz sintetizado com `espeak-ng`.
* Todos os 4 tipos de intenção, roteamento genérico vs. direto-a-contato, resolução de nome falado contra `contacts.json`, respeito à flag `enabled` da matriz de eventos, e a thread de orquestração completa — tudo com o transcritor mockado (texto fixo no lugar da inferência real), já que o **casamento de padrões é o núcleo do pedido** e isso não depende do modelo de ASR.

Se sua rede também bloquear esses hosts, defina `voice.model_dir` em `config.json` apontando para uma pasta com um modelo já convertido para o formato CTranslate2 (baixe em uma máquina com acesso e copie a pasta) — `SpeechTranscriber` usa esse caminho local em vez de tentar o download.

---

## 🗄️ Persistência de Eventos e Fila Durável de Notificações

`src/storage/` mitiga dois gaps reais que não estavam no plano original, mas apareceram assim que o sistema saiu da fase de PoC:

* **`event_log.py` (`EventLogger`):** até esta etapa, todo `EventNotification` disparado só aparecia via `print()` no console — sem nenhum registro persistente. Agora cada evento é gravado em `data/events.jsonl` (JSON Lines, somente-anexação), chamado tanto pelo laço de `main.py` quanto por `GuiBridge.tick()`/`test_alert()` na GUI. É a base da aba de histórico na GUI local (`get_initial_state` agora repopula o feed de alertas com `event_logger.read_recent()` na abertura, em vez de começar vazio) e do servidor remoto (ver seção abaixo) — e serve para auditoria manual do que aconteceu enquanto ninguém estava olhando.
* **Retenção e expurgo automático:** o log é somente-anexação por natureza (nunca sobrescreve), mas `config.json -> storage.retention_hours` (padrão 24h) define até quando um evento é mantido — `EventLogger.start_auto_purge()` roda em thread própria (intervalo `storage.purge_interval_minutes`, padrão 60min) e reescreve `data/events.jsonl` descartando o que expirou. Sem isso, uma instalação de longa duração acumularia indefinidamente dados sensíveis (frames de câmera anexados a alertas, rótulos de pessoa) em disco.
* **`notification_queue.py` (`PendingNotificationQueue`):** o retry com backoff exponencial do `WhatsAppNotifier` roda inteiramente em memória, numa thread. Se o processo caísse no meio de um backoff — ou mesmo depois de esgotar as tentativas — a notificação se perdia sem nenhum registro, e ninguém saberia que um alerta de queda não chegou ao cuidador. Agora `NotificationDispatcher.dispatch()` grava a notificação em `data/pending_notifications.json` **antes** de tentar o envio, e só a remove após confirmação de entrega (HTTP 2xx). No `main()`/`gui_main()`, `dispatcher.redeliver_pending()` roda no startup e tenta reenviar qualquer notificação que tenha ficado pendente de uma execução anterior.
* Ambos os arquivos ficam em `data/` (não versionado, adicionado ao `.gitignore` nesta etapa) e usam o mesmo padrão de override por variável de ambiente das demais configs (`KINESIS_EVENT_LOG`, `KINESIS_PENDING_QUEUE`).

**Achado real durante os testes desta etapa:** escrever um teste para o EVT-03 (inatividade) expôs um bug de inicialização no `EventEngine` — `_last_activity_at` era inicializado com `time.time()` real no construtor, e não com o `now` recebido em `update()`. Em produção isso não tinha efeito prático (os dois são sempre tempo real), mas quebrava silenciosamente qualquer cenário alimentado por timestamps simulados. Corrigido para inicializar de forma tardia, no primeiro `update()`.

---

## 🧪 Suíte de Testes (`tests/`)

Toda a validação até esta etapa era feita com scripts ad-hoc, descartados ao fim de cada sessão — sem nenhuma proteção contra regressão. `tests/` converte essa validação em uma suíte pytest committada (110+ testes): schemas/config (`test_config.py`), matriz de eventos (`test_event_engine.py`), cliente WhatsApp com HTTP mockado (`test_whatsapp_client.py`), `RateLimiter` (`test_rate_limiter.py`), persistência (`test_storage.py`, incluindo os cenários de "processo cai no meio do envio" com `redeliver_pending` e `dispatch_to_contact`), rastreamento de pessoa com YOLO mockado (`test_person_tracker.py`), `CameraPipeline` de ponta a ponta com câmera e detectores reais (`test_camera_pipeline.py`), detecção de objetos (`test_object_detector.py` — os testes que carregam o modelo real de ~600MB são pulados automaticamente se os pesos ainda não estiverem cacheados em `models/`), e o módulo de voz (`test_intent_matcher.py`, `test_camera_audio_capture.py` — inclui extração real via `ffmpeg` de áudio sintetizado com `espeak-ng`, `test_transcriber.py` e `test_voice_controller.py` — o teste com um modelo de ASR real é pulado a menos que `KINESIS_WHISPER_MODEL_DIR` esteja definido, ver seção do módulo de voz).

```bash
pip install -r requirements-dev.txt
pytest
```

---

## 🧍 Rastreamento Contínuo de Pessoa (ByteTrack)

`src/vision/person_tracker.py` (`PersonTracker`) mitiga o item **4.1.3** do plano original — na seção "Requisitos Não Viáveis ou Tecnicamente Inadequados" — sobre reidentificação facial biométrica constante ser pouco confiável em câmeras distantes ou em ângulos inclinados.

**A mitigação recomendada não é "melhorar a biometria", é trocar de abordagem:** em vez de tentar reconhecer o rosto da pessoa a cada frame, ela recebe um **ID de rastreamento contínuo** (ByteTrack) no momento em que entra no ambiente, mantido por continuidade de movimento/aparência enquanto permanece visível — sem depender de reconhecimento facial em nenhum momento.

* Usa **YOLOv8n padrão** (classe `person`, presente no COCO-80 — ao contrário de bengala/andador/cadeira de rodas, que exigiram YOLO-World) com o tracker **ByteTrack** embutido no `ultralytics` (`model.track(..., persist=True, tracker="bytetrack.yaml")`).
* **Habilitado por padrão** (`config.json → person_tracking.enabled = true`): o modelo é leve (~6MB, sem CLIP), e testes neste projeto mediram ~58ms por frame em CPU (640×480) — bem mais barato que a detecção de objetos (~800ms), então roda a todo frame (`frame_interval: 1`) por padrão.
* Substitui o antigo mock fixo `BehaviorTracker.registered_id = "Usuario_Principal"` pelo ID de rastreamento real da pessoa "principal" em cena (`Pessoa <track_id>`), escolhida por **estabilidade**: mantém a mesma pessoa entre frames mesmo que outra, com caixa maior, apareça — só troca quando o track anterior desaparece do campo de visão.
* Se mais de uma pessoa é detectada (ex: idoso + cuidador), a contagem aparece no HUD ("+N pessoa(s) no ambiente") — mas **o pipeline de pose/rosto/gestos continua analisando 1 pessoa por câmera** (o "principal" escolhido pelo tracker); não há, ainda, um `BehaviorTracker`/`EventEngine` independente por pessoa dentro da mesma câmera.
* `EventNotification` ganhou um campo `person_label`, propagado da mensagem de WhatsApp ao card de alerta na GUI — permite distinguir, no histórico, qual pessoa rastreada gerou cada evento.

> ⚠️ **Estado por câmera, nunca compartilhado.** Assim como os detectores MediaPipe (ver acima), o `persist=True` do ByteTrack mantém histórico de tracks associado à instância do modelo — compartilhar uma única instância entre câmeras misturaria tracks de cenas físicas independentes. Cada `CameraPipeline` cria sua própria `PersonTracker`.

**Validação neste ambiente:** sem pessoas reais em câmera, a lógica de escolha do track "principal" (estabilidade, troca ao desaparecer) foi validada com resultados do YOLO simulados (mock), e a integração completa (`CameraPipeline`, `EventEngine.person_label`, HUD, `GuiBridge`) foi exercitada de ponta a ponta com esses mocks. Com o modelo real e frames de ruído sintético (sem pessoas), o comportamento sem falso positivo foi confirmado — ao contrário do YOLO-World de `object_detection` (vocabulário aberto, mais propenso a ruído), o YOLOv8n padrão (classificador fechado) não detectou nada em ruído puro.

---

## ⏱️ Otimização de Taxa de Quadros por Pipeline

`src/vision/rate_limiter.py` (`RateLimiter`) mitiga o item **4.1.1** do plano original ("Processamento Multimodal Completo Simultâneo em Hardware Básico"): processar Pose (33 landmarks), Face Mesh (478 landmarks + 52 blendshapes) e Gesture a cada frame da câmera, sem GPU dedicada, sobrecarrega a CPU. O plano recomenda Pose a 15-20 FPS e Face/Blendshapes a 5-10 FPS — aplicamos o mesmo raciocínio ao Gesture (não especificado no plano, mas com custo comparável ao de Face).

* **Throttle por tempo de parede, não por contagem de frames** (`config.json → pipeline_rates`, default `pose_fps: 18`, `face_fps: 8`, `gesture_fps: 8`): cada detector MediaPipe só roda quando seu próprio limitador permite, no ritmo (Hz) configurado — independente da taxa de captura da câmera. Isso importa porque webcam (~30 FPS) e RTSP (Wi-Fi local ou remoto via VPN, sujeito a jitter de rede) têm taxas de captura bem diferentes; um throttle "a cada N frames" teria um FPS efetivo distinto em cada fonte, enquanto o throttle por segundos garante o mesmo ritmo em qualquer uma.
* **Entre execuções, reaproveita o último resultado**: os landmarks desenhados no frame e as métricas do HUD (`postura`, `expressão`, `gestos`) continuam vindo da última detecção real, não somem nem "piscam" nos frames em que o detector correspondente não rodou.
* **Ganho medido neste ambiente:** processando 60 frames sintéticos (640×480) em CPU, com a configuração padrão (18/8/8 fps) o pipeline completo levou 0.99s (~60 FPS efetivos); rodando os três detectores a cada frame (comportamento anterior a esta mitigação) levou 3.10s (~19 FPS efetivos) — **redução de ~68% no tempo de CPU** do pipeline de visão. Também validado que 90 frames processados a 30 FPS simulados geraram só 25 chamadas reais aos três detectores (13 Pose + 6 Face + 6 Gesture), com a taxa mantida entre webcam e RTSP simulados.
* **Contrapartida documentada, não escondida:** o histórico usado pela detecção de queda (`BehaviorTracker.hip_y_history`/`wrist_speed_history`, `history_len=15`) é contado em *amostras*, não em segundos. Reduzir `pose_fps` alarga a janela de tempo coberta por essas 15 amostras (ex: ~0.5s a 30 FPS vs ~0.83s a 18 FPS) — um efeito colateral esperado da mitigação, não um bug, mas que pode exigir recalibrar os limiares de queda se `pose_fps` for reduzido bem abaixo do padrão.

---

## 🦯 Detecção de Dispositivos de Mobilidade (YOLO-World)

`src/vision/object_detector.py` (`MobilityAidDetector`) identifica **bengala, andador e cadeira de rodas** no frame — objetos relevantes para avaliar risco de queda e rotina do idoso.

**Por que YOLO-World e não YOLOv8 "comum":** os pesos YOLOv8 pré-treinados padrão usam as 80 classes do COCO (pessoa, cadeira, sofá, TV...) e **não incluem bengala, andador nem cadeira de rodas em nenhuma delas**. Treinar um modelo customizado exigiria coletar e rotular um dataset próprio — fora de escopo. Em vez disso, usamos o **YOLO-World** (detecção de vocabulário aberto): cada caixa candidata é comparada contra embeddings de texto (CLIP) das classes que definimos em `MOBILITY_AID_PROMPTS`, permitindo detectar categorias arbitrárias por descrição textual.

* O encoder de texto padrão do YOLO-World (CLIP da OpenAI) baixa pesos de um host bloqueado pela política de rede usada para construir este projeto. A implementação troca automaticamente para o **MobileCLIP** (Apple), servido pelos releases do GitHub da Ultralytics — mesmo resultado, host compatível. Esse encoder só é usado **uma vez**, no carregamento (para calcular os embeddings das 3 classes); a detecção por frame usa somente o backbone YOLO.
* **Desabilitado por padrão** (`config.json → object_detection.enabled = false`): a dependência (`ultralytics`, que traz `torch`/`torchvision`) baixa ~600MB de pesos na primeira execução (modelo YOLO-World ~27MB + encoder de texto MobileCLIP ~570MB, cacheados em `models/`) — peso que nem todo dispositivo de borda deve pagar.
* `frame_interval` (padrão 5): a detecção **não** roda em todo frame — neste projeto, uma chamada em CPU levou ~800ms em imagens 640×480, então roda a cada N frames processados, mantendo a última detecção nos frames intermediários.
* `confidence` (padrão 0.35): em teste com ruído puro (imagem 100% aleatória, sem nenhum objeto real), o modelo ainda produziu falsos positivos com confiança de até ~0.31 — um limiar baixo deixa a leitura pouco confiável. **Este valor não foi calibrado contra fotos reais de bengala/andador/cadeira de rodas** (este ambiente não tem acesso a esse tipo de imagem) — ajuste com filmagem real do ambiente de instalação antes de confiar no resultado.
* As detecções aparecem como caixas rotuladas sobre o próprio frame (visível tanto nas janelas `cv2.imshow` quanto no `<canvas>` da GUI, sem nenhuma mudança adicional necessária) e na linha "Dispositivos:" do HUD. **Não** alimentam o `EventEngine` nem disparam notificações — dado que a confiança não foi validada contra imagens reais, avisar cuidadores com base nisso seria arriscado (falso alerta). É contexto visual, não um evento da matriz.
* Serve de base para o EVT-12 (zona de risco), que ainda depende de zonas espaciais configuráveis (ver Roteiro).

---

## 🖥️ GUI Desktop Híbrida (PyQt6 + QWebEngineView + QWebChannel)

`gui_main.py` é uma apresentação alternativa a `main.py`: em vez de janelas `cv2.imshow`, abre uma janela PyQt6 com um `QWebEngineView` carregando o dashboard local (`src/gui/web/index.html` via `file://`, **sem nenhum servidor HTTP**). Reaproveita `CameraPipeline`/`NotificationDispatcher` de `main.py` — a captura, visão, comportamento e eventos são exatamente os mesmos.

* **`src/gui/bridge.py` (`GuiBridge`):** `QObject` registrado no `QWebChannel` como `bridge`. Um `QTimer` (~30 Hz, em `main_window.py`) chama `bridge.tick()`, que processa todas as câmeras a cada iteração (eventos disparam para todas, mesmo as não exibidas), emitindo:
  * `frameReady(source_name, jpeg_base64)` — frame da câmera **selecionada**, para o `<canvas>` do dashboard.
  * `eventLogged(json)` — cada `EventNotification` disparado, para o feed de alertas.
  * `statusChanged(json)` — conectividade de câmeras/WhatsApp/voz, a cada 1s.
  * `metricsUpdated(json)` — FPS da câmera selecionada.
  * Slots invocáveis do JS: `get_initial_state`, `select_camera`, `set_event_enabled`, `set_event_contacts`, `add_contact`, `delete_contact`, `save_whatsapp_config`, `test_alert` — todos persistindo em `config.json`/`contacts.json` via `config/loader.py` quando aplicável.
* **`src/gui/web/`:** `index.html` + `styles.css` (Tailwind via CDN, dark mode) + `app.js`, com 4 abas: Dashboard (vídeo ao vivo + status + log de alertas), Matriz de Eventos (toggle/severidade/destinatários por evento, incluindo um botão "Testar"), Contatos (tabela + modal de cadastro) e Configuração (endpoint/token do WhatsApp).

> ⚠️ A aba Matriz de Eventos lista os 12 eventos da especificação, mas EVT-05/10/11/12 aparecem esmaecidos e desabilitados — a GUI não escapa a lacuna documentada na seção anterior, apenas a exibe.

**Validação neste ambiente:** sem display real, testado com `QT_QPA_PLATFORM=offscreen` — `QWebEngineView`/`QWebChannel` (incluído o roundtrip JS↔Python), todos os slots do bridge (com persistência real em disco) e o disparo de notificação WhatsApp de ponta a ponta (HTTP mockado) foram exercitados com sucesso, com os detectores MediaPipe reais processando frames sintéticos. `--no-sandbox`/`QTWEBENGINE_DISABLE_SANDBOX` só é necessário para rodar o Chromium embutido como root (caso deste sandbox); um usuário final comum não precisa disso.

---

## ✅ Check-in Programado ("Sistema OK")

Mitiga o gap de **continuidade do próprio monitoramento**: até esta etapa, o sistema só se comunicava quando algo dava errado (um evento da matriz). Se o processo travasse, a câmera perdesse sinal ou a energia caísse, ninguém era avisado — silêncio e "está tudo bem" eram indistinguíveis. `src/monitoring/checkin.py` (`CheckinScheduler`) fecha parte dessa lacuna: nos horários configurados em `config.json -> checkins.times` (padrão `08:00`, `14:00`, `20:00`), dispara uma notificação confirmando que o sistema está ativo — para os contatos de `checkins.notify_contact_ids`. Um check-in que deveria ter chegado e não chegou vira, por si só, um sinal para investigar.

* Reaproveita a mesma infraestrutura dos eventos de visão/voz: o check-in é um `EventNotification` (`event_id="CHECKIN"`, severidade `INFO`) despachado via `NotificationDispatcher.dispatch_to_contact()` — herda a fila durável de retry do WhatsApp e aparece no histórico de eventos (GUI local e servidor remoto) como qualquer outro evento.
* **A própria mensagem enviada reforça o limite da mitigação:** "Lembrete: isto é um apoio, não substitui visitas e ligações regulares." — o check-in não deve ser apresentado a ninguém como suficiente sozinho.
* **O que isto NÃO resolve:** se o processo do Kinesis cair ou a máquina perder energia, o check-in também para de ser enviado — a mesma limitação de qualquer notificação que depende do próprio processo estar de pé. Fechar esse caso de verdade exige um heartbeat vindo de **um segundo dispositivo**, com bateria e rede próprias (ex: um módulo celular independente), fora do escopo desta etapa — ver Roteiro.
* Desabilitado por padrão (`checkins.enabled = false`); os horários são validados no schema (`HH:MM`, 00–23 / 00–59).

---

## 📡 Servidor Remoto (acesso via VPN)

Mitiga o gap "o único jeito de ver o sistema é abrir o desktop PyQt6 na máquina instalada": um familiar/cuidador que não está fisicamente na casa só recebia texto de WhatsApp, sem histórico, tendência ou status ao vivo. `src/server/remote_server.py` (`RemoteStatusServer`) sobe um servidor HTTP **somente-leitura**, usando só a biblioteca padrão (`http.server` — nenhuma dependência nova), com:

* `GET /` — um dashboard HTML/CSS/JS autocontido (sem CDN externo) que pede o token uma vez (salvo em `localStorage`) e atualiza status/snapshot/histórico a cada poucos segundos.
* `GET /api/status` — conectividade e FPS de cada câmera, se o WhatsApp está configurado e se a voz está ativa (mesmo formato usado pela GUI local, via `src/monitoring/status.py`, extraído do `GuiBridge` para ser reaproveitado aqui sem depender do PyQt6).
* `GET /api/history?limit=N` — os últimos N eventos de `data/events.jsonl` (mesma fonte que alimenta o histórico da GUI local).
* `GET /api/snapshot/<camera>` — o último frame JPEG capturado daquela câmera (atualizado a `remote_server.snapshot_fps`, padrão 0,5 Hz — throttle próprio por câmera, reaproveitando `RateLimiter`).

**Modelo de acesso — leia antes de habilitar:** este servidor **não foi projetado para a internet pública**. O único controle de acesso é um token estático (`config.json -> remote_server.token`, obrigatório quando `enabled=true` — o schema recusa configurar um sem o outro). Isso é adequado para uma rede já autenticada por **VPN** — o mesmo túnel Tailscale já usado neste projeto para a câmera 5G remota (ver seção de hardware) — nunca para expor a porta diretamente na internet via port-forward do roteador. Configure `remote_server.host` para a interface atribuída pela VPN (ou deixe em `127.0.0.1` enquanto o acesso remoto ainda não é necessário) e distribua o token apenas aos familiares que devem ter acesso.

Desabilitado por padrão (`remote_server.enabled = false`).

---

## ▶️ Como Executar

```bash
# Dependencia de sistema para o modulo de voz (extracao de audio de RTSP);
# opcional se voice.enabled permanecer false.
apt-get install -y ffmpeg

pip install -r requirements.txt
# (dev/testes: pip install -r requirements-dev.txt)

cp config/config.example.json config/config.json      # edite cameras/eventos/whatsapp/deteccao/rastreamento/voz
cp config/contacts.example.json config/contacts.json  # edite os contatos reais

python main.py       # janelas cv2.imshow (uma por câmera)
# ou
python gui_main.py   # dashboard PyQt6 + HTML/CSS/JS
```

Em `main.py`, pressione `q` ou `Esc` em qualquer janela de vídeo para encerrar todas as fontes. Em `gui_main.py`, basta fechar a janela.

---

## 🛣️ Roteiro (próximas fases, fora do escopo desta entrega)

* **Validar o módulo de voz com um modelo de ASR real e áudio de câmera real** — bloqueado neste ambiente (Hugging Face Hub, Azure CDN da OpenAI e alphacephei.com/Vosk todos retornaram 403 de política de rede); ver seção do módulo de voz para como habilitar com `voice.model_dir` numa rede sem esse bloqueio.
* **VAD (Voice Activity Detection, ex: Silero) antes da transcrição** — hoje `VoiceController` transcreve toda janela de `chunk_seconds`, mesmo silêncio; um VAD evitaria chamadas de ASR desnecessárias.
* **Confirmação falada e TTS** (Piper) para o fluxo completo de ditado de mensagem do plano original — o escopo desta etapa foi deliberadamente restrito a identificação por palavra-chave + envio direto, sem diálogo.
* **Zonas espaciais configuráveis** no frame (cama, escada, fogão) — destrava EVT-10 e EVT-12 (a detecção de objetos que EVT-12 também precisa já existe, ver seção acima).
* **Calibração de `object_detection.confidence`** contra fotos/filmagem reais de bengala, andador e cadeira de rodas — não pôde ser feita neste ambiente.
* **`BehaviorTracker`/`EventEngine` por pessoa** (não só por câmera): hoje, com múltiplas pessoas em cena, o rastreamento (ByteTrack) sabe distinguir cada uma, mas a análise de pose/rosto/gestos ainda segue só a pessoa "principal" escolhida.
* **Validação de `PersonTracker`/ByteTrack com pessoas reais em câmera** — só foi possível validar a lógica de escolha do track principal com resultados YOLO mockados; o comportamento com movimento/oclusão reais ainda não foi observado.
* **Recalibrar os limiares de detecção de queda caso `pipeline_rates.pose_fps` seja reduzido** — o histórico de quadris/punhos é contado em amostras, não segundos (ver seção de otimização de frame rate acima).
* **CI (GitHub Actions ou similar) rodando `pytest` a cada push** — a suíte existe e passa localmente, mas nada a executa automaticamente ainda.
* **Heartbeat externo, independente de energia/rede da residência** — o Check-in Programado (ver seção acima) e o Servidor Remoto fecham parte do gap de continuidade do monitoramento, mas ambos ainda dependem do próprio processo/máquina do Kinesis estar de pé. Fechar isso de verdade exige um segundo dispositivo (ex: módulo celular com bateria própria) checando o Kinesis de fora — fora do escopo desta etapa.
* **Escalonamento multicanal de alertas** — hoje todo alerta (evento ou check-in) converge para o WhatsApp; sem um fallback por SMS/ligação nem uma escada de escalonamento ("se o primeiro contato não confirmar em N minutos, notifica o próximo"), um problema no gateway configurado significa nenhum alerta chegar a ninguém.

---

## 🌐 Setup de Hardware (Câmera 5G Remota via Tailscale)

1. **Câmera + roteador remoto:** insira o chip 5G no roteador (ex: GL.iNet GL-X3000), conecte a câmera IP (ex: Intelbras Mibo iM4-C) ao Wi-Fi gerado por ele via app Mibo Smart, e anote o IP local e a chave de acesso da câmera (etiqueta sob a base).
2. **VPN no roteador:** no painel do roteador (`192.168.8.1`), vá em VPN → Tailscale, faça login na sua conta, habilite *Allow Subnet Routes* para a sub-rede da câmera (ex: `192.168.8.0/24`) e aprove a rota em [login.tailscale.com](https://login.tailscale.com) → Machines → Edit route settings.
3. **Computador principal:** instale o cliente Tailscale, faça login com a mesma conta e ative a VPN — a partir daí o computador acessa a câmera remota pelo IP de sub-rede, através do túnel criptografado sobre 5G.
