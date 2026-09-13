"""Servidor HTTP local somente-leitura para acesso remoto de um familiar/
cuidador que não está na máquina onde o Kinesis roda — mitiga o gap "único
jeito de ver o sistema é abrir o desktop PyQt6 na máquina instalada" (ver
README/roteiro de evolução): status ao vivo, histórico de eventos e um
snapshot periódico de cada câmera, acessíveis por um navegador comum.

Usa só a biblioteca padrão (`http.server`) — sem trazer uma dependência
nova (Flask/FastAPI) para um recurso opcional e somente-leitura.

⚠️ NÃO é pensado para a internet pública. O único controle de acesso é um
token estático (`KINESIS_REMOTE_SERVER_TOKEN` no ambiente, ou
`config.json -> remote_server.token` — ver `RemoteServerConfig.resolve_token`
em config/schemas.py), adequado para uma rede já autenticada por VPN (ex:
Tailscale — o mesmo túnel já usado para a câmera remota, ver README), não
para exposição direta na internet. Aponte `remote_server.host` para a
interface da VPN e nunca faça port-forward desta porta no roteador.
"""
import json
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

from src.vision.rate_limiter import RateLimiter

DASHBOARD_HTML = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kinesis SMA-TR - Acesso Remoto</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: #0f1712; color: #e7efe9;
    font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
    padding: 16px;
  }
  h1 { font-size: 1.1rem; margin: 0 0 4px; }
  .sub { color: #93a69b; font-size: 0.8rem; margin-bottom: 18px; }
  .gate { max-width: 340px; margin: 15vh auto; text-align: center; }
  .gate input { width: 100%; padding: 10px; border-radius: 8px; border: 1px solid #2b3d33;
    background: #16241c; color: #e7efe9; margin-bottom: 10px; font-size: 0.95rem; }
  .gate button, .logout { padding: 10px 16px; border-radius: 8px; border: none;
    background: #2b6e6e; color: white; font-weight: 600; cursor: pointer; font-size: 0.9rem; }
  [hidden] { display: none !important; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; margin-bottom: 22px; }
  .card { background: #16241c; border: 1px solid #2b3d33; border-radius: 10px; padding: 12px; }
  .card img { width: 100%; border-radius: 6px; background: #0a100c; aspect-ratio: 4/3; object-fit: cover; }
  .cam-name { font-weight: 600; font-size: 0.9rem; margin-bottom: 6px; }
  .pill { display: inline-flex; align-items: center; gap: 5px; font-size: 0.7rem; font-weight: 600;
    padding: 2px 8px; border-radius: 99px; }
  .pill-ok { background: #1c3d38; color: #7fd8cb; }
  .pill-bad { background: #3a2321; color: #e08a80; }
  .top-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; flex-wrap: wrap; gap: 8px; }
  table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  th, td { text-align: left; padding: 7px 8px; border-bottom: 1px solid #223028; vertical-align: top; }
  th { color: #93a69b; font-weight: 600; font-size: 0.7rem; text-transform: uppercase; }
  .sev { padding: 1px 7px; border-radius: 99px; font-size: 0.68rem; font-weight: 700; white-space: nowrap; }
  .sev-CRITICAL { background: #3a2321; color: #e08a80; }
  .sev-HIGH { background: #3a2f18; color: #e0b374; }
  .sev-MEDIUM { background: #33301a; color: #d3c473; }
  .sev-LOW, .sev-INFO { background: #1e2e3a; color: #8fb8dc; }
  .err { color: #e08a80; font-size: 0.85rem; margin-top: 8px; }
  .empty { color: #6d7d74; font-size: 0.85rem; padding: 10px 0; }
</style>
</head>
<body>

<div id="gate" class="gate">
  <h1>Kinesis SMA-TR</h1>
  <div class="sub">Acesso remoto (somente leitura)</div>
  <input id="token-input" type="password" placeholder="Token de acesso">
  <button onclick="saveToken()">Entrar</button>
  <div id="gate-err" class="err"></div>
</div>

<div id="dashboard" hidden>
  <div class="top-row">
    <div>
      <h1>Kinesis SMA-TR</h1>
      <div class="sub" id="server-time">-</div>
    </div>
    <button class="logout" onclick="logout()">Sair</button>
  </div>

  <div id="cameras" class="grid"></div>

  <h1>Historico recente</h1>
  <div id="history-empty" class="empty" hidden>Nenhum evento registrado ainda.</div>
  <table id="history-table" hidden>
    <thead><tr><th>Quando</th><th>Evento</th><th>Severidade</th><th>Origem</th><th>Mensagem</th></tr></thead>
    <tbody id="history-body"></tbody>
  </table>
</div>

<script>
function getToken() { return localStorage.getItem("kinesis_token") || ""; }
function saveToken() {
  const value = document.getElementById("token-input").value.trim();
  if (!value) return;
  localStorage.setItem("kinesis_token", value);
  boot();
}
function logout() {
  localStorage.removeItem("kinesis_token");
  location.reload();
}

async function apiGet(path) {
  const token = getToken();
  const sep = path.includes("?") ? "&" : "?";
  const response = await fetch(path + sep + "token=" + encodeURIComponent(token), {
    headers: { "Authorization": "Bearer " + token },
  });
  if (response.status === 401) throw new Error("unauthorized");
  if (!response.ok) throw new Error("http_" + response.status);
  return response.json();
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function refreshStatus() {
  let status;
  try {
    status = await apiGet("/api/status");
  } catch (e) {
    if (e.message === "unauthorized") { logout(); return; }
    return;
  }
  document.getElementById("server-time").textContent =
    "Atualizado " + new Date().toLocaleTimeString("pt-BR");

  const container = document.getElementById("cameras");
  container.innerHTML = "";
  for (const cam of status.cameras) {
    const card = document.createElement("div");
    card.className = "card";
    const token = encodeURIComponent(getToken());
    const src = "/api/snapshot/" + encodeURIComponent(cam.name) + "?token=" + token + "&t=" + Date.now();
    card.innerHTML = `
      <div class="cam-name">${escapeHtml(cam.name)}</div>
      <img src="${src}" onerror="this.style.display='none'">
      <div style="margin-top:8px; display:flex; justify-content:space-between; align-items:center;">
        <span class="pill ${cam.connected ? "pill-ok" : "pill-bad"}">${cam.connected ? "Conectada" : "Sem sinal"}</span>
        <span style="font-size:0.75rem; color:#93a69b;">${cam.fps} fps</span>
      </div>`;
    container.appendChild(card);
  }
  const whatsappPill = status.whatsapp_configured ? "WhatsApp configurado" : "WhatsApp NAO configurado";
  const voicePill = status.voice_available ? "Voz ativa" : "Voz desativada";
  const sub = document.getElementById("server-time");
  sub.textContent += ` | ${whatsappPill} | ${voicePill}`;
}

async function refreshHistory() {
  let data;
  try {
    data = await apiGet("/api/history?limit=100");
  } catch (e) {
    if (e.message === "unauthorized") { logout(); return; }
    return;
  }
  const events = (data.events || []).slice().reverse();
  const table = document.getElementById("history-table");
  const empty = document.getElementById("history-empty");
  const body = document.getElementById("history-body");
  if (events.length === 0) {
    table.hidden = true; empty.hidden = false;
    return;
  }
  table.hidden = false; empty.hidden = true;
  body.innerHTML = events.map((ev) => {
    const when = new Date(ev.timestamp).toLocaleString("pt-BR");
    return `<tr>
      <td>${when}</td>
      <td>${escapeHtml(ev.event_id)} - ${escapeHtml(ev.name)}${ev.person_label ? " (" + escapeHtml(ev.person_label) + ")" : ""}</td>
      <td><span class="sev sev-${escapeHtml(ev.severity)}">${escapeHtml(ev.severity)}</span></td>
      <td>${escapeHtml(ev.source_name)}</td>
      <td>${escapeHtml(ev.message)}</td>
    </tr>`;
  }).join("");
}

let pollHandle = null;
function applyTokenFromUrlIfPresent() {
  // Permite abrir um link "http://host:porta/?token=..." (ex: recebido por
  // WhatsApp quando o servidor sobe, ver src/monitoring/remote_server_notice.py)
  // sem precisar digitar o token manualmente. Salva em localStorage e limpa
  // a URL visivel para o token nao ficar exposto na barra de enderecos.
  const params = new URLSearchParams(location.search);
  const urlToken = params.get("token");
  if (!urlToken) return;
  localStorage.setItem("kinesis_token", urlToken);
  params.delete("token");
  const rest = params.toString();
  history.replaceState(null, "", location.pathname + (rest ? "?" + rest : ""));
}
function boot() {
  applyTokenFromUrlIfPresent();
  const token = getToken();
  document.getElementById("gate").hidden = !!token;
  document.getElementById("dashboard").hidden = !token;
  if (!token) return;
  refreshStatus();
  refreshHistory();
  if (pollHandle) clearInterval(pollHandle);
  pollHandle = setInterval(() => { refreshStatus(); refreshHistory(); }, 5000);
}
boot();
</script>
</body>
</html>
"""


class RemoteStatusServer:
    """Servidor de leitura (status/histórico/snapshot) rodando numa thread
    própria. `status_provider`/`history_provider` são callbacks fornecidos
    por main.py/gui_main.py, chamados a cada requisição — mantém este
    módulo sem dependência direta de `CameraPipeline`/`EventLogger`."""

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        status_provider: Callable[[], dict],
        history_provider: Callable[[int], List[dict]],
        snapshot_fps: float = 0.5,
    ):
        if not token:
            raise ValueError("RemoteStatusServer requer um token nao vazio.")
        self.host = host
        self.port = port
        self.token = token
        self.status_provider = status_provider
        self.history_provider = history_provider
        self.snapshot_fps = snapshot_fps

        self._snapshot_lock = threading.Lock()
        self._snapshots: Dict[str, bytes] = {}
        self._snapshot_rates: Dict[str, RateLimiter] = {}

        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # --- captura de snapshot (chamado pelo laço principal / GuiBridge.tick) ---

    def should_capture_snapshot(self, camera_name: str, now: Optional[float] = None) -> bool:
        """Throttle por câmera (RateLimiter próprio, ver src/vision/) para
        que o chamador só pague o custo de `cv2.imencode` quando o
        snapshot for de fato renovado."""
        now = now if now is not None else time.time()
        limiter = self._snapshot_rates.setdefault(camera_name, RateLimiter(self.snapshot_fps))
        return limiter.should_run(now)

    def set_snapshot(self, camera_name: str, jpeg_bytes: bytes) -> None:
        with self._snapshot_lock:
            self._snapshots[camera_name] = jpeg_bytes

    def _get_snapshot(self, camera_name: str) -> Optional[bytes]:
        with self._snapshot_lock:
            return self._snapshots.get(camera_name)

    # --- ciclo de vida do servidor HTTP ---

    def start(self) -> None:
        if self._httpd is not None:
            return
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass  # o Kinesis ja loga o que importa; silencia o log padrao do http.server

            def _authorized(self, query: dict) -> bool:
                auth_header = self.headers.get("Authorization", "")
                if auth_header == f"Bearer {server.token}":
                    return True
                return query.get("token", [None])[0] == server.token

            def _send_json(self, status: int, payload: dict) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_bytes(self, status: int, content_type: str, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802 (nome exigido pela stdlib)
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)

                if parsed.path == "/api/health":
                    self._send_json(HTTPStatus.OK, {"ok": True, "time": time.time()})
                    return

                if parsed.path == "/":
                    self._send_bytes(HTTPStatus.OK, "text/html; charset=utf-8", DASHBOARD_HTML.encode("utf-8"))
                    return

                if not self._authorized(query):
                    self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "token invalido ou ausente"})
                    return

                if parsed.path == "/api/status":
                    self._send_json(HTTPStatus.OK, server.status_provider())
                    return

                if parsed.path == "/api/history":
                    try:
                        limit = int(query.get("limit", ["100"])[0])
                    except ValueError:
                        limit = 100
                    self._send_json(HTTPStatus.OK, {"events": server.history_provider(limit)})
                    return

                if parsed.path.startswith("/api/snapshot/"):
                    camera_name = unquote(parsed.path[len("/api/snapshot/"):])
                    jpeg_bytes = server._get_snapshot(camera_name)
                    if jpeg_bytes is None:
                        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "sem snapshot ainda para esta camera"})
                        return
                    self._send_bytes(HTTPStatus.OK, "image/jpeg", jpeg_bytes)
                    return

                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "rota desconhecida"})

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = self._httpd.server_address[1]  # relevante quando port=0 (porta efêmera, usado nos testes)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        print(
            f"Servidor remoto (leitura) escutando em http://{self.host}:{self.port} - "
            "acesse via VPN; nunca exponha esta porta na internet publica."
        )

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
