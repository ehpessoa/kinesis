# 👁️ Kinesis — SMA-TR (Sistema de Monitoramento Assistencial de Idosos em Tempo Real)

O **Kinesis / SMA-TR** é uma solução de monitoramento comportamental e assistencial em tempo real, evoluindo de uma PoC de visão computacional para uma arquitetura modular Edge/Local-First: captura multi-fonte (webcam, RTSP local, RTSP remoto via VPN), análise multimodal com MediaPipe, um motor de eventos com severidade e disparo de alertas via WhatsApp — tudo processado localmente, sem enviar vídeo para serviços externos.

---

## 🏗️ Arquitetura Atual

```
config/            -> config.json / contacts.json (não versionados) + schemas Pydantic
src/capture/       -> ThreadedCamera: captura em thread própria, com reconexão automática
src/vision/        -> detectores MediaPipe + MobilityAidDetector (YOLO-World)
src/behavior/      -> BehaviorTracker (cinemática/expressões) + EventEngine (matriz de eventos)
src/notifications/ -> WhatsAppNotifier (HTTPS + retry com backoff exponencial)
src/gui/           -> GuiBridge (QWebChannel) + MainWindow (PyQt6) + web/ (HTML/CSS/JS)
main.py            -> orquestra N CameraPipeline (1 por câmera) via janelas cv2.imshow
gui_main.py        -> mesma orquestração, apresentada como dashboard PyQt6/HTML
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

## ▶️ Como Executar

```bash
pip install opencv-python mediapipe numpy pydantic httpx PyQt6 PyQt6-WebEngine
pip install ultralytics  # opcional: só necessário se object_detection.enabled=true

cp config/config.example.json config/config.json      # edite cameras/eventos/whatsapp/deteccao de objetos
cp config/contacts.example.json config/contacts.json  # edite os contatos reais

python main.py       # janelas cv2.imshow (uma por câmera)
# ou
python gui_main.py   # dashboard PyQt6 + HTML/CSS/JS
```

Em `main.py`, pressione `q` ou `Esc` em qualquer janela de vídeo para encerrar todas as fontes. Em `gui_main.py`, basta fechar a janela.

---

## 🛣️ Roteiro (próximas fases, fora do escopo desta entrega)

* **Módulo de voz offline** (Silero VAD + faster-whisper + Piper TTS + NLU) para comandos de mensagem por voz.
* **Zonas espaciais configuráveis** no frame (cama, escada, fogão) — destrava EVT-10 e EVT-12 (a detecção de objetos que EVT-12 também precisa já existe, ver seção acima).
* **Calibração de `object_detection.confidence`** contra fotos/filmagem reais de bengala, andador e cadeira de rodas — não pôde ser feita neste ambiente.
* **Otimização de frame-rate por pipeline** (Pose 15-20 FPS / Face 5-10 FPS) para hardware sem GPU — a detecção de objetos já tem seu próprio throttle (`frame_interval`), mas os detectores MediaPipe ainda rodam em todo frame.
* **`requirements.txt`/`pyproject.toml` e suíte de testes automatizada** (a validação atual é ad-hoc, não commitada como testes).

O módulo de voz em particular exige microfone real para validação de verdade — não foi portado nesta etapa.

---

## 🌐 Setup de Hardware (Câmera 5G Remota via Tailscale)

1. **Câmera + roteador remoto:** insira o chip 5G no roteador (ex: GL.iNet GL-X3000), conecte a câmera IP (ex: Intelbras Mibo iM4-C) ao Wi-Fi gerado por ele via app Mibo Smart, e anote o IP local e a chave de acesso da câmera (etiqueta sob a base).
2. **VPN no roteador:** no painel do roteador (`192.168.8.1`), vá em VPN → Tailscale, faça login na sua conta, habilite *Allow Subnet Routes* para a sub-rede da câmera (ex: `192.168.8.0/24`) e aprove a rota em [login.tailscale.com](https://login.tailscale.com) → Machines → Edit route settings.
3. **Computador principal:** instale o cliente Tailscale, faça login com a mesma conta e ative a VPN — a partir daí o computador acessa a câmera remota pelo IP de sub-rede, através do túnel criptografado sobre 5G.
