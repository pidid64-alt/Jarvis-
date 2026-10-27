// Джарвис — десктопное окно (Electron).
// Запускает Python-ядро (jarvis web) как внутренний процесс и показывает
// интерфейс в нативном окне: без браузера, без отдельной консоли.
//
// Запуск:  npm start   (в папке gui/)

const { app, BrowserWindow, Menu } = require("electron");
const { spawn } = require("child_process");
const http = require("http");
const path = require("path");

const PORT = 8123;
const PROJECT_ROOT = path.join(__dirname, "..");
const PYTHON = path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe");

let pyProc = null;
let quitting = false;

function startBackend() {
  pyProc = spawn(PYTHON, ["-m", "jarvis", "web", "--port", String(PORT)], {
    cwd: PROJECT_ROOT,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  pyProc.stdout.on("data", (d) => console.log("[ядро]", d.toString().trim()));
  pyProc.stderr.on("data", (d) => console.error("[ядро]", d.toString().trim()));
  pyProc.on("exit", (code) => {
    console.log("[ядро] остановлено, код", code);
    pyProc = null;
  });
}

function pingOnce(timeoutMs = 1500) {
  return new Promise((resolve, reject) => {
    const req = http.get(`http://127.0.0.1:${PORT}/api/status`, {timeout: timeoutMs}, (res) => {
      res.resume();
      resolve();
    });
    req.on("timeout", () => { req.destroy(); reject(new Error("timeout")); });
    req.on("error", reject);
  });
}

async function waitBackend(maxMs = 180000) {
  // Холодный старт Python (импорты, память, MCP) может занять минуту-две.
  const started = Date.now();
  let lastError = null;
  while (Date.now() - started < maxMs && !quitting) {
    try {
      await pingOnce();
      return;
    } catch (e) {
      lastError = e;
      await new Promise((r) => setTimeout(r, 700));
    }
  }
  throw lastError || new Error("ядро не поднялось за отведённое время");
}

async function createWindow() {
  startBackend();
  const win = new BrowserWindow({
    width: 1120,
    height: 760,
    minWidth: 720,
    minHeight: 520,
    backgroundColor: "#05080f",
    title: "Джарвис",
    autoHideMenuBar: true,
    show: false, // показываем после загрузки — без «мигания»
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  // Разрешаем микрофон для голосового ввода (без лишних диалогов).
  win.webContents.session.setPermissionRequestHandler((wc, permission, callback) => {
    callback(permission === "media");
  });
  Menu.setApplicationMenu(null);

  // Сразу показываем окно с экраном загрузки — пользователь видит, что идёт работа.
  await win.loadFile(path.join(__dirname, "loading.html"));
  win.show();
  try {
    await waitBackend();
    await win.loadURL(`http://127.0.0.1:${PORT}/`);
    console.log("[окно] интерфейс загружен");
  } catch (e) {
    // Ядро так и не поднялось — объясняем, что делать.
    await win.loadURL(
      "data:text/html;charset=utf-8," +
        encodeURIComponent(
          "<body style='background:#05080f;color:#dbe6f5;font-family:Segoe UI;display:flex;" +
            "align-items:center;justify-content:center;height:100vh;flex-direction:column;gap:10px'>" +
            "<h2>Ядро Джарвиса не запустилось</h2>" +
            "<p style='color:#8291b8'>Запусти вручную и посмотри ошибку:<br>" +
            "<code>.venv\\Scripts\\python.exe -m jarvis web --port 8123</code> в папке проекта</p></body>"
        )
    );
  }
  return win;
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    const [win] = BrowserWindow.getAllWindows();
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });

  app.whenReady().then(async () => {
    await createWindow();
    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });
}

app.on("window-all-closed", () => {
  quitting = true;
  if (pyProc) pyProc.kill();
  app.quit();
});

app.on("before-quit", () => {
  quitting = true;
  if (pyProc) pyProc.kill();
});
