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

function waitBackend(tries = 90) {
  // Ждём готовности FastAPI (загрузка модели/памяти занимает время).
  return new Promise((resolve, reject) => {
    const ping = (n) => {
      if (quitting) return reject(new Error("закрыто"));
      const req = http.get(`http://127.0.0.1:${PORT}/api/status`, (res) => {
        res.resume();
        resolve();
      });
      req.on("error", () => {
        if (n <= 0) reject(new Error("ядро не поднялось"));
        else setTimeout(() => ping(n - 1), 500);
      });
    };
    ping(tries);
  });
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
  Menu.setApplicationMenu(null);

  try {
    await waitBackend();
    await win.loadURL(`http://127.0.0.1:${PORT}/`);
  } catch (e) {
    // Ядро не поднялось — показываем понятную заглушку вместо белого экрана.
    await win.loadURL(
      "data:text/html;charset=utf-8," +
        encodeURIComponent(
          "<body style='background:#05080f;color:#dbe6f5;font-family:Segoe UI;display:flex;" +
            "align-items:center;justify-content:center;height:100vh;flex-direction:column;gap:10px'>" +
            "<h2>Не удалось запустить ядро Джарвиса</h2>" +
            "<p style='color:#8291b8'>Проверьте, что окружение собрано: <code>uv sync</code> в папке проекта</p></body>"
        )
    );
  }
  win.show();
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
