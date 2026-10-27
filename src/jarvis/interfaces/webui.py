"""Веб-интерфейс Джарвиса v2: FastAPI + SSE + самодостаточная страница.

Дизайн: тёмная «стеклянная» тема в стиле ИИ-ассистентов, анимированный
реактор, живые статусы инструментов, markdown-ответы, подтверждения кнопками.

Запуск: `jarvis web` → http://127.0.0.1:8080
       `jarvis web --host 0.0.0.0` → плюс доступ с телефона в той же Wi-Fi.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from jarvis.core.tools.base import ConfirmRequest
from jarvis.interfaces.runtime import Runtime

log = logging.getLogger("jarvis.webui")

_CONFIRM_TIMEOUT = 600.0


@dataclass
class WebHub:
    """Широковещательные события для браузера + подтверждения кнопками."""

    clients: set = field(default_factory=set)
    pending: dict = field(default_factory=dict)
    busy: bool = False

    async def publish(self, event: dict) -> None:
        for queue in list(self.clients):
            try:
                queue.put_nowait(event)
            except Exception:
                self.clients.discard(queue)

    async def stream(self):
        queue: asyncio.Queue = asyncio.Queue()
        self.clients.add(queue)
        try:
            yield ": connected\n\n"
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            self.clients.discard(queue)

    async def confirm(self, request: ConfirmRequest) -> bool:
        request_id = uuid.uuid4().hex[:12]
        await self.publish(
            {
                "type": "confirm",
                "id": request_id,
                "tool": request.tool.name,
                "reason": request.reason,
                "args": request.args,
            }
        )
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self.pending[request_id] = future
        try:
            return await asyncio.wait_for(future, timeout=_CONFIRM_TIMEOUT)
        except TimeoutError:
            await self.publish({"type": "confirm_expired", "id": request_id})
            return False
        finally:
            self.pending.pop(request_id, None)

    def resolve(self, request_id: str, approved: bool) -> bool:
        future = self.pending.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(approved)
        return True


def _make_observer(hub: WebHub):
    def observer(event: str, **details) -> None:
        tool = details.get("tool", "")
        payload: dict = {"type": f"tool_{event}", "tool": tool}
        if event == "error":
            payload["error"] = details.get("error", "")
        if event == "done":
            payload["seconds"] = round(details.get("seconds", 0), 1)
        asyncio.ensure_future(hub.publish(payload))

    return observer


def create_app(runtime: Runtime) -> FastAPI:
    app = FastAPI(title="Jarvis Web UI")
    hub = WebHub()
    state = {"session": "web-main"}

    settings = runtime.settings
    info = {
        "model": settings.llm_model,
        "provider": settings.llm_provider,
        "tools": len(runtime.registry.all()),
        "telegram": bool(runtime.telegram),
        "google": runtime.google is not None and runtime.google.authorized,
        "scheduler": runtime.scheduler is not None,
    }

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(INDEX_HTML)

    @app.get("/api/status")
    async def status() -> JSONResponse:
        return JSONResponse({"busy": hub.busy, **info})

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        return StreamingResponse(
            hub.stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/chat")
    async def chat(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(
                {"status": "error", "message": "некорректный JSON (нужен UTF-8)"}, status_code=400
            )
        text = str(payload.get("text", "")).strip()
        if not text:
            return JSONResponse({"status": "error", "message": "пустой запрос"}, status_code=400)
        if hub.busy:
            return JSONResponse({"status": "busy"}, status_code=409)
        hub.busy = True

        async def run() -> None:
            try:
                await hub.publish({"type": "thinking"})
                assert runtime.agent is not None
                runtime.agent._confirmer = hub.confirm
                runtime.registry._observer = _make_observer(hub)
                try:
                    answer = await runtime.agent.handle(text, state["session"])
                finally:
                    runtime.agent._confirmer = None
                    runtime.registry._observer = None
                await hub.publish({"type": "answer", "text": answer})
            except Exception as e:
                log.exception("Ошибка обработки веб-сообщения")
                await hub.publish({"type": "answer", "text": f"[Ошибка] {e}"})
            finally:
                hub.busy = False

        asyncio.create_task(run())
        return JSONResponse({"status": "ok"})

    @app.post("/api/confirm")
    async def confirm(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"status": "error"}, status_code=400)
        ok = hub.resolve(str(payload.get("id", "")), bool(payload.get("approved")))
        return JSONResponse({"status": "ok" if ok else "stale"})

    @app.post("/api/reset")
    async def reset() -> JSONResponse:
        state["session"] = f"web-{uuid.uuid4().hex[:8]}"
        await hub.publish({"type": "reset"})
        return JSONResponse({"status": "ok"})

    return app


INDEX_HTML = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Джарвис</title>
<style>
  :root {
    --bg: #05080f;
    --glass: rgba(18, 28, 50, .55);
    --glass-border: rgba(120, 170, 255, .14);
    --text: #e3ecfb; --dim: #8291b8;
    --accent: #3fd2ff; --accent2: #2b7fff;
    --ok: #3ddc84; --warn: #ffb454; --bad: #ff5d5d;
    --radius: 18px;
  }
  * { box-sizing: border-box; margin: 0; }
  html, body { height: 100%; }
  body {
    background:
      radial-gradient(900px 500px at 15% -10%, rgba(43,127,255,.22), transparent 60%),
      radial-gradient(900px 600px at 90% 110%, rgba(53,208,255,.12), transparent 60%),
      var(--bg);
    color: var(--text);
    font: 15px/1.6 "Segoe UI", system-ui, -apple-system, sans-serif;
    display: flex; justify-content: center; overflow: hidden;
  }
  .app {
    width: min(1100px, 100%); height: 100%;
    display: grid; grid-template-columns: 260px 1fr; gap: 14px; padding: 14px;
  }
  .side { display: flex; flex-direction: column; gap: 12px; min-height: 0; }
  .glass {
    background: var(--glass); border: 1px solid var(--glass-border);
    border-radius: var(--radius); backdrop-filter: blur(18px);
    box-shadow: 0 8px 32px rgba(0,0,0,.35);
  }
  .brand { padding: 18px; display: flex; align-items: center; gap: 14px; }
  .orb-wrap { position: relative; width: 52px; height: 52px; flex-shrink: 0; }
  .orb {
    position: absolute; inset: 6px; border-radius: 50%;
    background: radial-gradient(circle at 32% 28%, #b8f1ff, var(--accent2) 58%, #0a1a33);
    box-shadow: 0 0 22px rgba(53,208,255,.6);
    animation: pulse 3.2s ease-in-out infinite;
  }
  .ring {
    position: absolute; inset: 0; border-radius: 50%;
    border: 1px solid rgba(53,208,255,.45);
    animation: ripple 3.2s linear infinite;
  }
  .ring:nth-child(2) { animation-delay: 1.6s; }
  @keyframes pulse { 50% { transform: scale(1.06); } }
  @keyframes ripple { 0% { transform: scale(.7); opacity: .9; } 100% { transform: scale(1.35); opacity: 0; } }
  .brand h1 { font-size: 21px; letter-spacing: 4px; font-weight: 650; }
  .brand h1 b { color: var(--accent); font-weight: 650; }
  .brand small { color: var(--dim); font-size: 11px; letter-spacing: 1.5px; }
  .panel { padding: 14px 16px; }
  .panel h2 { font-size: 11px; text-transform: uppercase; letter-spacing: 2px; color: var(--dim); margin-bottom: 10px; }
  .row { display: flex; justify-content: space-between; font-size: 13px; padding: 5px 0; }
  .row span:last-child { color: var(--dim); }
  .on { color: var(--ok) !important; } .off { color: var(--dim) !important; }
  .btn {
    width: 100%; margin-top: 4px; padding: 11px; border-radius: 12px; border: 1px solid var(--glass-border);
    background: rgba(53,208,255,.08); color: var(--text); font: inherit; font-size: 13.5px; cursor: pointer;
    transition: background .15s, border-color .15s;
  }
  .btn:hover { background: rgba(53,208,255,.16); border-color: rgba(53,208,255,.4); }
  .side .spacer { flex: 1; }
  .foot { padding: 12px 16px; font-size: 11px; color: var(--dim); }
  .main { display: flex; flex-direction: column; min-height: 0; gap: 12px; }
  #chat {
    flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 12px;
    padding: 20px; scroll-behavior: smooth;
  }
  #chat::-webkit-scrollbar { width: 8px; }
  #chat::-webkit-scrollbar-thumb { background: rgba(120,170,255,.18); border-radius: 4px; }
  .bubble {
    max-width: 78%; padding: 12px 16px; border-radius: 18px;
    animation: slide .22s ease-out;
    word-break: break-word; white-space: pre-wrap;
  }
  @keyframes slide { from { opacity: 0; transform: translateY(8px); } }
  .user { align-self: flex-end; background: linear-gradient(135deg, #2b6fe3, #2b9fff); color: #fff;
          border-radius: 18px 18px 6px 18px; }
  .bot { align-self: flex-start; background: var(--glass); border: 1px solid var(--glass-border);
         backdrop-filter: blur(18px); border-radius: 18px 18px 18px 6px; }
  .bot b { color: var(--accent); } .bot code { font-family: Consolas, monospace; background: rgba(0,0,0,.35);
         padding: 1px 6px; border-radius: 6px; font-size: 13px; color: #9fe9ff; }
  .event { align-self: center; font: 12px Consolas, monospace; color: var(--dim);
           background: rgba(10,16,30,.5); border: 1px solid rgba(120,170,255,.08);
           padding: 4px 12px; border-radius: 20px; animation: slide .2s ease-out; }
  .event .ok { color: var(--ok); } .event .bad { color: var(--bad); }
  .dots { align-self: flex-start; display: flex; gap: 5px; padding: 14px 16px; }
  .dots i { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); animation: bounce 1.2s infinite; }
  .dots i:nth-child(2) { animation-delay: .18s; } .dots i:nth-child(3) { animation-delay: .36s; }
  @keyframes bounce { 30% { transform: translateY(-6px); opacity: .5; } }
  .confirm {
    align-self: stretch; background: rgba(255,180,84,.07); border: 1px solid rgba(255,180,84,.35);
    border-radius: 14px; padding: 14px 16px; animation: slide .25s ease-out;
  }
  .confirm h3 { font-size: 13.5px; color: var(--warn); margin-bottom: 4px; }
  .confirm .why { font-size: 12.5px; color: var(--dim); }
  .confirm pre { font-size: 12px; color: var(--dim); background: rgba(0,0,0,.3); padding: 10px;
                 border-radius: 10px; margin: 10px 0; max-height: 170px; overflow: auto; white-space: pre-wrap; }
  .confirm .btns { display: flex; gap: 10px; }
  .confirm button { flex: 1; padding: 10px; border-radius: 10px; border: 0; font: inherit;
                    font-weight: 600; cursor: pointer; }
  .confirm .yes { background: linear-gradient(135deg, #21b563, #3ddc84); color: #04160c; }
  .confirm .no { background: rgba(255,93,93,.12); color: var(--bad); border: 1px solid rgba(255,93,93,.4); }
  .confirm.done { opacity: .5; pointer-events: none; }
  .verdict { font-weight: 600; }
  footer { display: flex; gap: 10px; }
  #input {
    flex: 1; background: var(--glass); color: var(--text); border: 1px solid var(--glass-border);
    border-radius: 16px; padding: 14px 18px; font: inherit; outline: none; backdrop-filter: blur(18px);
    transition: border-color .15s, box-shadow .15s;
  }
  #input:focus { border-color: rgba(53,208,255,.5); box-shadow: 0 0 0 3px rgba(53,208,255,.12); }
  #send {
    width: 54px; border: 0; border-radius: 16px; cursor: pointer; font-size: 20px;
    background: linear-gradient(135deg, var(--accent2), var(--accent)); color: #051220;
    transition: filter .15s, transform .1s;
  }
  #send:hover { filter: brightness(1.15); }
  #send:active { transform: scale(.95); }
  #send:disabled { opacity: .35; cursor: default; }
  @media (max-width: 760px) {
    .app { grid-template-columns: 1fr; }
    .side { display: none; }
  }
</style>
</head>
<body>
<div class="app">
  <aside class="side">
    <div class="glass brand">
      <div class="orb-wrap"><div class="ring"></div><div class="ring"></div><div class="orb"></div></div>
      <div><h1>JAR<b>VIS</b></h1><small>личный ассистент</small></div>
    </div>
    <div class="glass panel">
      <h2>Система</h2>
      <div class="row"><span>Модель</span><span id="st-model">…</span></div>
      <div class="row"><span>Инструменты</span><span id="st-tools">…</span></div>
      <div class="row"><span>Google</span><span id="st-google">…</span></div>
      <div class="row"><span>Telegram</span><span id="st-tg">…</span></div>
      <div class="row"><span>Планировщик</span><span id="st-sched">…</span></div>
    </div>
    <div class="glass panel">
      <h2>Действия</h2>
      <button class="btn" id="reset">🧹 Новый диалог</button>
      <button class="btn" id="unread">📬 Что на почте?</button>
      <button class="btn" id="brief">☕ Брифинг</button>
    </div>
    <div class="spacer"></div>
    <div class="glass foot">Подключение: <span id="conn">устанавливается…</span></div>
  </aside>

  <main class="main">
    <div id="chat" class="glass"><div class="event">Спроси что угодно — инструменты подключены</div></div>
    <footer>
      <input id="input" placeholder="Напиши сообщение Джарвису…" autocomplete="off">
      <button id="send">➤</button>
    </footer>
  </main>
</div>

<script>
const chat = document.getElementById('chat');
const input = document.getElementById('input');
const send = document.getElementById('send');
let busy = false;

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s ?? '';
  return d.innerHTML;
}
// Мини-markdown для ответов: **жирный**, `код`, переносы строк.
function md(text) {
  let html = esc(text);
  html = html.replace(/`([^`\\n]+)`/g, '<code>$1</code>');
  html = html.replace(/\\*\\*([^*\\n]+)\\*\\*/g, '<b>$1</b>');
  return html;
}
function push(html, cls) {
  const el = document.createElement('div');
  el.className = cls; el.innerHTML = html;
  chat.appendChild(el);
  chat.scrollTop = chat.scrollHeight;
  return el;
}
const addMsg = (cls, text, isMd) => push(isMd ? md(text) : esc(text), 'bubble ' + cls);
const addEvent = (html) => push(html, 'event');
const dots = () => push('<i></i><i></i><i></i>', 'dots');

const addConfirm = (c) => {
  const card = push(
    '<h3>⚠️ Подтвердите: ' + esc(c.tool) + '</h3>'
    + '<div class="why">' + esc(c.reason) + '</div>'
    + '<pre>' + esc(JSON.stringify(c.args, null, 2)) + '</pre>'
    + '<div class="btns"><button class="yes">✅ Выполнить</button><button class="no">❌ Отмена</button></div>',
    'confirm'
  );
  const decide = async (approved) => {
    await fetch('/api/confirm', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: c.id, approved})});
    card.classList.add('done');
    card.querySelector('.btns').outerHTML = '<div class="verdict">'
      + (approved ? '✅ Выполнено' : '❌ Отклонено') + '</div>';
  };
  card.querySelector('.yes').onclick = () => decide(true);
  card.querySelector('.no').onclick = () => decide(false);
};

const es = new EventSource('/api/events');
es.onopen = () => document.getElementById('conn').textContent = 'подключено ✅';
es.onerror = () => document.getElementById('conn').textContent = 'переподключение…';
es.onmessage = (e) => {
  const ev = JSON.parse(e.data);
  if (ev.type === 'thinking') { chat.querySelector('.dots')?.remove(); dots(); }
  else if (ev.type === 'answer') {
    chat.querySelector('.dots')?.remove();
    addMsg('bot', ev.text, true);
    setBusy(false);
  }
  else if (ev.type === 'tool_start') addEvent('⚙ ' + esc(ev.tool) + '…');
  else if (ev.type === 'tool_done') addEvent('<span class="ok">✓ ' + esc(ev.tool) + ' · ' + ev.seconds + ' с</span>');
  else if (ev.type === 'tool_error') addEvent('<span class="bad">✗ ' + esc(ev.tool) + ': ' + esc(ev.error || '') + '</span>');
  else if (ev.type === 'tool_denied') addEvent('<span class="bad">✗ ' + esc(ev.tool) + ' — отклонено</span>');
  else if (ev.type === 'confirm') addConfirm(ev);
  else if (ev.type === 'reset') push('🧹 Контекст диалога очищен', 'event');
};

fetch('/api/status').then(r => r.json()).then(s => {
  document.getElementById('st-model').textContent = s.model || '—';
  document.getElementById('st-tools').textContent = s.tools;
  const mark = (id, on, label) => {
    const el = document.getElementById(id);
    el.textContent = on ? label : 'нет';
    el.className = on ? 'on' : 'off';
  };
  mark('st-google', s.google, 'OAuth ✅');
  mark('st-tg', s.telegram, 'подключён ✅');
  mark('st-sched', s.scheduler, 'работает ✅');
});

function setBusy(v) { busy = v; send.disabled = v; input.disabled = v; if (!v) input.focus(); }
async function submit() {
  const text = input.value.trim();
  if (!text || busy) return;
  addMsg('user', text, false);
  input.value = '';
  setBusy(true);
  const r = await fetch('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text})});
  if (r.status === 409) { addEvent('Джарвис ещё отвечает на предыдущий запрос…'); setBusy(false); }
  else if (r.status === 400) { addEvent('Пустой запрос'); setBusy(false); }
}
send.onclick = submit;
input.addEventListener('keydown', e => { if (e.key === 'Enter') submit(); });

document.getElementById('reset').onclick = async () => {
  await fetch('/api/reset', {method: 'POST'});
};
document.getElementById('unread').onclick = () => {
  input.value = 'Что нового на почте?';
  submit();
};
document.getElementById('brief').onclick = () => {
  input.value = 'Сделай краткий обзор: что на почте и что запланировано?';
  submit();
};
setBusy(false);
</script>
</body>
</html>"""
