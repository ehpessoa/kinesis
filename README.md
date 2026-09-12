# 👁️ Kinesis — SMA-TR (Sistema de Monitoramento Assistencial de Idosos em Tempo Real)

O **Kinesis / SMA-TR** é uma solução de monitoramento comportamental e assistencial em tempo real, evoluindo de uma PoC de visão computacional para uma arquitetura modular Edge/Local-First: captura multi-fonte (webcam, RTSP local, RTSP remoto via VPN), análise multimodal com MediaPipe, um motor de eventos com severidade e disparo de alertas via WhatsApp — tudo processado localmente, sem enviar vídeo para serviços externos.

---

## 🏗️ Arquitetura Atual

```
config/            -> config.json / contacts.json (não versionados) + schemas Pydantic
src/capture/       -> ThreadedCamera: captura em thread própria, com reconexão automática
src/vision/        -> detectores MediaPipe (download de modelos, desenho de landmarks)
src/behavior/      -> BehaviorTracker (cinemática/expressões) + EventEngine (matriz de eventos)
src/notifications/ -> WhatsAppNotifier (HTTPS + retry com backoff exponencial)
main.py            -> orquestra N CameraPipeline (1 por câmera) e despacha notificações
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

## 🛣️ Roteiro (próximas fases, fora do escopo desta entrega)

* **Detecção de objetos (YOLOv8)** para bengalas/andadores e zonas de risco (pré-requisito de EVT-12).
* **Módulo de voz offline** (Silero VAD + faster-whisper + Piper TTS + NLU) para comandos de mensagem por voz.
* **GUI Desktop Híbrida** (PyQt6 + QWebEngineView + QWebChannel + HTML/CSS/JS), substituindo as janelas `cv2.imshow` atuais.

Essas três fases exigem dependências pesadas (PyQt6-WebEngine, Whisper, Piper), microfone e display reais para validação — não foram portadas nesta etapa para não entregar código não verificável como se estivesse pronto.

---

## ▶️ Como Executar

```bash
pip install opencv-python mediapipe numpy pydantic httpx
cp config/config.example.json config/config.json      # edite cameras/eventos/whatsapp
cp config/contacts.example.json config/contacts.json  # edite os contatos reais
python main.py
```

Pressione `q` ou `Esc` em qualquer janela de vídeo para encerrar todas as fontes.

---

## 🌐 Setup de Hardware (Câmera 5G Remota via Tailscale)

1. **Câmera + roteador remoto:** insira o chip 5G no roteador (ex: GL.iNet GL-X3000), conecte a câmera IP (ex: Intelbras Mibo iM4-C) ao Wi-Fi gerado por ele via app Mibo Smart, e anote o IP local e a chave de acesso da câmera (etiqueta sob a base).
2. **VPN no roteador:** no painel do roteador (`192.168.8.1`), vá em VPN → Tailscale, faça login na sua conta, habilite *Allow Subnet Routes* para a sub-rede da câmera (ex: `192.168.8.0/24`) e aprove a rota em [login.tailscale.com](https://login.tailscale.com) → Machines → Edit route settings.
3. **Computador principal:** instale o cliente Tailscale, faça login com a mesma conta e ative a VPN — a partir daí o computador acessa a câmera remota pelo IP de sub-rede, através do túnel criptografado sobre 5G.
