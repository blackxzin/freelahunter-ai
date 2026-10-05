"""Local operator panel for selecting and controlling a marketplace hunter.

The panel deliberately starts the existing Chromium hunter instead of
reimplementing browser automation.  It binds to localhost by default and
keeps external submission disabled for every process it starts.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "platforms.json"


@dataclass(frozen=True)
class PlatformRuntime:
    name: str
    label: str
    max_jobs: int
    interval_minutes: int
    locale: str
    currency: str
    profile_path: str
    listing_url: str
    allow_submission: bool


def load_platforms(config_path: str | Path = DEFAULT_CONFIG) -> dict[str, PlatformRuntime]:
    """Load the supported platform metadata used by the operator panel."""
    raw = json.loads(Path(config_path).read_text(encoding="utf-8"))
    runtimes: dict[str, PlatformRuntime] = {}
    labels = {"99freelas": "99Freelas", "upwork": "Upwork"}
    defaults = {
        "99freelas": (5, 15),
        "upwork": (2, 12),
    }
    for name, config in raw.items():
        if name not in defaults:
            continue
        max_jobs, interval = defaults[name]
        runtimes[name] = PlatformRuntime(
            name=name,
            label=labels.get(name, name.title()),
            max_jobs=max_jobs,
            interval_minutes=interval,
            locale=str(config.get("locale", "pt-BR")),
            currency=str(config.get("currency", "BRL")),
            profile_path=str(config.get("profile_path", "")),
            listing_url=str(config.get("listing_url", "")),
            allow_submission=bool(config.get("allow_submission", False)),
        )
    if not runtimes:
        raise ValueError("Nenhuma plataforma suportada foi encontrada na configuração.")
    return runtimes


def hunter_environment(platform: PlatformRuntime, base: dict[str, str] | None = None) -> dict[str, str]:
    """Return a safe environment for one platform hunter."""
    env = dict(base or os.environ)
    env.update({
        "PLATFORM": platform.name,
        "MAX_JOBS": str(platform.max_jobs),
        "HUNT_INTERVAL_MINUTES": str(platform.interval_minutes),
        "RUN_FOREVER": "true",
        "AUTO_SEND": "false",
        "DRY_RUN": "true",
        "AUTOMATION_MODE": "SEMI_AUTO",
        "AUTO_LOGIN_PROMPT": "true",
    })
    return env


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def request_allowed(host: str | None, origin: str | None) -> bool:
    """Reject DNS-rebinding and cross-site requests aimed at the local panel."""
    if urlsplit(f"//{host or ''}").hostname not in LOCAL_HOSTS:
        return False
    return origin is None or urlsplit(origin).netloc == host


def hunter_command(root: str | Path = ROOT) -> list[str]:
    """Build the process command without invoking a shell."""
    return ["node", str(Path(root) / "scripts" / "interactive_hunt.mjs")]


class HunterProcessManager:
    """Own at most one child hunter and expose a small JSON-friendly status."""

    def __init__(self, root: str | Path = ROOT, platforms: dict[str, PlatformRuntime] | None = None):
        self.root = Path(root)
        self.platforms = platforms or load_platforms(self.root / "config" / "platforms.json")
        self._process: subprocess.Popen[str] | None = None
        self._platform: str | None = None
        self._started_at: float | None = None
        self._last_exit_code: int | None = None
        self._logs: deque[str] = deque(maxlen=240)
        self._lock = threading.RLock()

    def _read_output(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        try:
            for line in process.stdout:
                with self._lock:
                    self._logs.append(line.rstrip())
        finally:
            process.stdout.close()

    def _refresh(self) -> None:
        if self._process is not None and self._process.poll() is not None:
            self._last_exit_code = self._process.returncode
            self._process = None
            self._platform = None
            self._started_at = None

    def start(self, platform_name: str) -> dict[str, Any]:
        with self._lock:
            self._refresh()
            if platform_name not in self.platforms:
                raise ValueError(f"Plataforma não suportada: {platform_name}")
            if self._process is not None:
                raise RuntimeError("Já existe uma caça em execução. Pare-a antes de trocar de plataforma.")
            platform = self.platforms[platform_name]
            env = hunter_environment(platform)
            self._logs.clear()
            self._logs.append(f"Iniciando {platform.label}: {platform.max_jobs} vaga(s) por ciclo; intervalo de {platform.interval_minutes} min.")
            process = subprocess.Popen(
                hunter_command(self.root),
                cwd=self.root,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                start_new_session=True,
            )
            self._process = process
            self._platform = platform_name
            self._started_at = time.time()
            threading.Thread(target=self._read_output, args=(process,), daemon=True).start()
            return self.status()

    def continue_input(self) -> bool:
        with self._lock:
            self._refresh()
            if self._process is None or self._process.stdin is None:
                return False
            try:
                self._process.stdin.write("\n")
                self._process.stdin.flush()
                return True
            except (BrokenPipeError, OSError):
                return False

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self._refresh()
            process = self._process
            if process is None:
                return self.status()
            if process.poll() is None:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                except (AttributeError, ProcessLookupError, PermissionError):
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    except (AttributeError, ProcessLookupError, PermissionError):
                        process.kill()
                    process.wait(timeout=5)
            self._last_exit_code = process.returncode
            self._process = None
            self._platform = None
            self._started_at = None
            self._logs.append(f"Caça parada (código {self._last_exit_code}).")
            return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._refresh()
            platform = self.platforms.get(self._platform) if self._platform else None
            return {
                "running": self._process is not None,
                "pid": self._process.pid if self._process is not None else None,
                "platform": self._platform,
                "platform_label": platform.label if platform else None,
                "started_at": self._started_at,
                "last_exit_code": self._last_exit_code,
                "logs": list(self._logs),
            }


def _page(platforms: dict[str, PlatformRuntime]) -> str:
    cards = []
    for platform in platforms.values():
        if platform.name == "upwork":
            description = "Jobs internacionais · propostas em inglês · moeda da vaga"
        else:
            description = "Jobs brasileiros · propostas em português · valores em reais"
        badge = "INTERNACIONAL" if platform.name == "upwork" else "BRASIL"
        mark = "U" if platform.name == "upwork" else "99"
        cards.append(
            f'''<button class="platform" data-platform="{platform.name}" data-label="{platform.label}"
              data-description="{description}" data-rules="{platform.max_jobs} vaga(s) a cada {platform.interval_minutes} minutos"
              aria-pressed="false">
              <span class="platform-top"><span class="platform-mark {platform.name}">{mark}</span><span class="platform-badge">{badge}</span><span class="select-dot" aria-hidden="true"></span></span>
              <span class="platform-name">{platform.label}</span>
              <span class="platform-description">{description}</span>
              <span class="platform-stats"><span><b>{platform.max_jobs}</b> vagas</span><span><b>{platform.interval_minutes}m</b> intervalo</span><span><b>{platform.currency}</b> base</span></span>
              <span class="platform-cta">Selecionar plataforma <span aria-hidden="true">↗</span></span>
            </button>'''
        )
    cards_html = "".join(cards)
    return f'''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FreelaHunter · Central de caça</title>
<style>
  :root {{ color-scheme: dark; font: 15px/1.5 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:#0c0f14; color:#f4f1ea; --line:#29313d; --muted:#8f99a8; --panel:#131820; --panel-2:#171e28; --amber:#f4b860; --blue:#72b7ff; --green:#72d3a0; --red:#ff8a7b; }}
  * {{ box-sizing:border-box; }} body {{ min-height:100vh; margin:0; background:radial-gradient(circle at 80% -20%, #263446 0, transparent 38%), #0c0f14; }}
  body:before {{ content:""; position:fixed; inset:0; pointer-events:none; opacity:.18; background-image:linear-gradient(rgba(255,255,255,.035) 1px, transparent 1px),linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px); background-size:36px 36px; mask-image:linear-gradient(to bottom, black, transparent 80%); }}
  .shell {{ position:relative; max-width:1080px; margin:0 auto; padding:28px 22px 54px; }}
  .topbar {{ display:flex; justify-content:space-between; align-items:center; gap:20px; margin-bottom:52px; }}
  .brand {{ display:flex; align-items:center; gap:11px; font-weight:800; letter-spacing:-.02em; }} .brand-mark {{ display:grid; place-items:center; width:34px; height:34px; border-radius:10px; color:#101319; background:var(--amber); font-size:13px; font-weight:900; box-shadow:0 0 0 5px rgba(244,184,96,.08); }}
  .live {{ display:flex; align-items:center; gap:8px; color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.11em; }} .live:before {{ content:""; width:7px; height:7px; background:var(--green); border-radius:50%; box-shadow:0 0 12px var(--green); }}
  .eyebrow {{ color:var(--amber); text-transform:uppercase; font-size:11px; font-weight:800; letter-spacing:.18em; margin:0 0 10px; }}
  h1 {{ max-width:700px; margin:0; font-size:clamp(32px,5vw,55px); line-height:1.03; letter-spacing:-.055em; }} h1 em {{ color:var(--amber); font-style:normal; }} h2 {{ margin:0; font-size:14px; letter-spacing:.02em; }} p {{ color:var(--muted); }} .intro {{ max-width:650px; margin:15px 0 37px; font-size:16px; }}
  .section-label {{ display:flex; align-items:center; justify-content:space-between; gap:14px; margin-bottom:13px; color:#c8d0dc; }} .step {{ color:var(--muted); font-size:11px; font-weight:800; letter-spacing:.13em; text-transform:uppercase; }}
  .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; margin-bottom:28px; }}
  .platform {{ min-height:236px; text-align:left; color:inherit; background:linear-gradient(145deg, rgba(28,37,49,.96), rgba(17,22,30,.96)); border:1px solid var(--line); border-radius:17px; padding:20px; cursor:pointer; display:flex; flex-direction:column; gap:10px; transition:transform .18s ease,border-color .18s ease,background .18s ease,box-shadow .18s ease; }}
  .platform:hover {{ transform:translateY(-2px); border-color:#526276; background:#1a2430; }} .platform.selected {{ border-color:var(--amber); box-shadow:0 0 0 1px var(--amber), 0 15px 34px rgba(0,0,0,.2); background:linear-gradient(145deg, #252d37, #171d26); }}
  .platform:focus-visible, button.action:focus-visible {{ outline:3px solid rgba(114,183,255,.65); outline-offset:3px; }}
  .platform-top {{ display:flex; align-items:center; gap:10px; margin-bottom:6px; }} .platform-mark {{ display:grid; place-items:center; width:40px; height:40px; border-radius:12px; background:#262f3b; color:#fff; font-weight:900; letter-spacing:-.06em; }} .platform-mark.upwork {{ background:#d9f77b; color:#18210c; }} .platform-mark.99freelas {{ background:#f2a35b; color:#321a0c; font-size:12px; }}
  .platform-badge {{ color:var(--muted); font-size:10px; font-weight:800; letter-spacing:.14em; }} .select-dot {{ width:12px; height:12px; margin-left:auto; border:1px solid #596575; border-radius:50%; }} .selected .select-dot {{ background:var(--amber); border-color:var(--amber); box-shadow:inset 0 0 0 3px #252d37; }}
  .platform-name {{ font-size:24px; font-weight:800; letter-spacing:-.04em; }} .platform-description {{ min-height:45px; color:var(--muted); font-size:13px; }} .platform-stats {{ display:flex; gap:18px; padding-top:12px; border-top:1px solid rgba(255,255,255,.08); color:var(--muted); font-size:12px; }} .platform-stats b {{ display:block; color:#edf0f4; font-size:16px; letter-spacing:-.03em; }} .platform-cta {{ margin-top:auto; color:var(--amber); font-size:12px; font-weight:800; }} .platform-cta span {{ display:inline-block; margin-left:3px; transition:transform .18s ease; }} .platform:hover .platform-cta span {{ transform:translate(3px,-2px); }}
  .status {{ display:grid; grid-template-columns:minmax(0,1fr) 220px; gap:22px; border:1px solid var(--line); border-radius:17px; padding:21px; background:rgba(19,24,32,.9); box-shadow:0 18px 45px rgba(0,0,0,.16); }} .status-main {{ min-width:0; }} .status-head {{ display:flex; align-items:center; gap:10px; min-width:0; }} .status-head strong {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:20px; letter-spacing:-.035em; }}
  .pill {{ display:inline-flex; align-items:center; gap:7px; border-radius:999px; padding:5px 9px; color:var(--muted); background:#202936; font-size:10px; font-weight:900; letter-spacing:.12em; text-transform:uppercase; }} .pill:before {{ content:""; width:6px; height:6px; border-radius:50%; background:#748092; }} .pill.running {{ color:#b9f2d4; background:#133b2b; }} .pill.running:before {{ background:var(--green); box-shadow:0 0 8px var(--green); }}
  #details {{ margin:9px 0 20px; font-size:13px; }} .toolbar {{ display:flex; flex-wrap:wrap; gap:9px; align-items:center; }} button.action {{ border:1px solid transparent; border-radius:9px; padding:11px 15px; cursor:pointer; font:inherit; font-size:13px; font-weight:800; transition:transform .15s ease,filter .15s ease,opacity .15s ease; }} button.action:hover:not(:disabled) {{ transform:translateY(-1px); filter:brightness(1.08); }} #start {{ background:var(--amber); color:#20170b; }} #stop {{ background:transparent; border-color:#713d3a; color:#ffaaa0; }} #continue {{ background:#233951; border-color:#385b82; color:#a7d3ff; }} button:disabled {{ opacity:.35; cursor:not-allowed; }}
  .status-side {{ border-left:1px solid var(--line); padding-left:22px; }} .side-title {{ color:var(--muted); font-size:10px; font-weight:900; letter-spacing:.14em; text-transform:uppercase; }} .side-list {{ display:grid; gap:11px; margin-top:14px; }} .side-item {{ display:flex; justify-content:space-between; gap:12px; color:var(--muted); font-size:12px; }} .side-item b {{ color:#e9edf3; font-weight:700; text-align:right; }}
  .log-wrap {{ margin-top:18px; border-top:1px solid var(--line); padding-top:17px; }} .log-head {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:9px; color:var(--muted); font-size:10px; font-weight:900; letter-spacing:.14em; text-transform:uppercase; }} .log-head span {{ color:#657184; font-weight:700; letter-spacing:.04em; }} pre {{ min-height:88px; max-height:280px; margin:0; white-space:pre-wrap; overflow:auto; background:#0b0e13; border:1px solid #202731; border-radius:10px; padding:13px; color:#b7c2d2; font:12px/1.65 ui-monospace,SFMono-Regular,Menlo,monospace; }} .hint {{ grid-column:1 / -1; margin:0; padding-top:15px; border-top:1px solid var(--line); font-size:12px; }} .hint strong {{ color:#dce3ed; }}
  @media (max-width:700px) {{ .shell {{ padding:20px 15px 35px; }} .topbar {{ margin-bottom:37px; }} .grid {{ grid-template-columns:1fr; }} .status {{ grid-template-columns:1fr; }} .status-side {{ border-left:0; border-top:1px solid var(--line); padding:17px 0 0; }} .status-head strong {{ font-size:17px; }} .toolbar button {{ flex:1 1 100%; }} }}
</style></head><body><main class="shell">
<header class="topbar"><div class="brand"><span class="brand-mark">FH</span><span>FreelaHunter</span></div><span class="live">painel local</span></header>
<p class="eyebrow">Central de operações</p><h1>Encontre o próximo projeto <em>certo.</em></h1><p class="intro">Escolha uma plataforma e deixe o navegador trabalhar. A caça busca vagas, entende o escopo e prepara o rascunho da proposta para revisão.</p>
<div class="section-label"><h2>Escolha onde procurar</h2><span class="step">01 · plataforma</span></div><section class="grid">{cards_html}</section>
<section class="status"><div class="status-main"><div class="status-head"><span id="state" class="pill">parado</span><strong id="current">Nenhuma plataforma selecionada</strong></div>
<p id="details">Clique em um cartão para selecionar a plataforma.</p><div class="toolbar"><button id="start" class="action">Caçar</button><button id="stop" class="action">Parar caça</button><button id="continue" class="action">Continuar após login</button></div>
<div class="log-wrap"><div class="log-head">Atividade ao vivo <span>atualiza automaticamente</span></div><pre id="logs">Aguardando uma plataforma…</pre></div></div>
<aside class="status-side"><div class="side-title">Fluxo protegido</div><div class="side-list"><div class="side-item"><span>Busca e análise</span><b>automático</b></div><div class="side-item"><span>Rascunho</span><b>preparado</b></div><div class="side-item"><span>Envio externo</span><b>manual</b></div><div class="side-item"><span>Login e desafios</span><b>manual</b></div></div></aside>
<p class="hint"><strong>Nota:</strong> o botão Caçar inicia o processo contínuo. A conta, Google, CAPTCHA e desafios da plataforma continuam sob confirmação manual; o envio de propostas não é automático.</p></section></main>
<script>
let selected = null;
const cards = [...document.querySelectorAll('.platform')];
const state = document.querySelector('#state'), current = document.querySelector('#current'), details = document.querySelector('#details'), logs = document.querySelector('#logs');
const start = document.querySelector('#start'), stop = document.querySelector('#stop'), cont = document.querySelector('#continue');
cards.forEach(card => card.addEventListener('click', () => {{ selected = card.dataset.platform; cards.forEach(item => {{ item.classList.toggle('selected', item === card); item.setAttribute('aria-pressed', item === card ? 'true' : 'false'); }}); refresh(); }}));
async function refresh() {{
  const response = await fetch('/api/status'); const data = await response.json();
  if (data.platform && !selected) {{ selected = data.platform; const active = cards.find(item => item.dataset.platform === selected); if (active) {{ active.classList.add('selected'); active.setAttribute('aria-pressed', 'true'); }} }}
  state.textContent = data.running ? 'rodando' : 'parado'; state.classList.toggle('running', data.running);
  const card = cards.find(item => item.dataset.platform === (data.platform || selected));
  current.textContent = data.platform_label || (card ? card.dataset.label : 'Nenhuma plataforma selecionada');
  details.textContent = data.running
    ? `PID ${{data.pid}} · ${{card?.dataset.rules || 'processo controlado pelo painel'}}`
    : (card ? `${{card.dataset.description}} · ${{card.dataset.rules}}.` : 'Selecione uma plataforma para iniciar.');
  logs.textContent = (data.logs || []).join('\\n') || 'Sem logs ainda.';
  start.disabled = data.running || !selected; stop.disabled = !data.running; cont.disabled = !data.running;
  start.textContent = selected ? `Caçar ${{cards.find(item => item.dataset.platform === selected)?.dataset.label || ''}}` : 'Caçar';
}}
start.onclick = async () => {{ if (!selected) return; const r = await fetch('/api/start/' + selected, {{method:'POST'}}); const d = await r.json(); if (!r.ok) alert(d.detail || 'Não foi possível iniciar'); refresh(); }};
stop.onclick = async () => {{ await fetch('/api/stop', {{method:'POST'}}); refresh(); }};
cont.onclick = async () => {{ await fetch('/api/continue', {{method:'POST'}}); refresh(); }};
setInterval(refresh, 2500); refresh();
</script></body></html>'''


def create_operator_app(manager: HunterProcessManager | None = None):
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import HTMLResponse, JSONResponse
    except ImportError:
        return None

    controller = manager or HunterProcessManager()
    app = FastAPI(title="FreelaHunter Operator Panel")
    app.state.hunter_manager = controller

    @app.middleware("http")
    async def local_only(request, call_next):
        if not request_allowed(request.headers.get("host"), request.headers.get("origin")):
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return _page(controller.platforms)

    @app.get("/api/platforms")
    def platforms():
        return [platform.__dict__ for platform in controller.platforms.values()]

    @app.get("/api/status")
    def status():
        return controller.status()

    @app.post("/api/start/{platform_name}")
    def start(platform_name: str):
        try:
            return controller.start(platform_name)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/api/stop")
    def stop():
        return controller.stop()

    @app.post("/api/continue")
    def continue_input():
        return {"sent": controller.continue_input()}

    return app


def serve_operator_panel(host: str = "127.0.0.1", port: int = 8765,
                         manager: HunterProcessManager | None = None) -> None:
    """Serve the panel with stdlib when optional web dependencies are absent."""
    controller = manager or HunterProcessManager()
    try:
        import uvicorn
    except ImportError:
        uvicorn = None
    app = create_operator_app(controller)
    if app is not None and uvicorn is not None:
        uvicorn.run(app, host=host, port=port)
        return

    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import unquote, urlparse

    class Handler(BaseHTTPRequestHandler):
        def _send(self, payload: Any, status: int = 200, content_type: str = "application/json"):
            if content_type == "text/html":
                body = payload.encode("utf-8")
            else:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
            if not request_allowed(self.headers.get("Host"), self.headers.get("Origin")):
                return self._send({"detail": "forbidden"}, 403)
            path = urlparse(self.path).path
            if path == "/":
                self._send(_page(controller.platforms), content_type="text/html")
            elif path == "/api/status":
                self._send(controller.status())
            elif path == "/api/platforms":
                self._send([platform.__dict__ for platform in controller.platforms.values()])
            else:
                self._send({"detail": "not found"}, 404)

        def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
            if not request_allowed(self.headers.get("Host"), self.headers.get("Origin")):
                return self._send({"detail": "forbidden"}, 403)
            path = urlparse(self.path).path
            try:
                if path.startswith("/api/start/"):
                    result = controller.start(unquote(path.rsplit("/", 1)[-1]))
                elif path == "/api/stop":
                    result = controller.stop()
                elif path == "/api/continue":
                    result = {"sent": controller.continue_input()}
                else:
                    self._send({"detail": "not found"}, 404)
                    return
                self._send(result)
            except ValueError as error:
                self._send({"detail": str(error)}, 404)
            except RuntimeError as error:
                self._send({"detail": str(error)}, 409)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"FreelaHunter painel disponível em http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        controller.stop()
        server.server_close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Painel local do FreelaHunter")
    parser.add_argument("--host", default="127.0.0.1", help="Por segurança, mantenha localhost")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve_operator_panel(args.host, args.port)
