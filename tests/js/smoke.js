/* Прогон страницы окна без браузера: настоящий app.js против настоящего API.
 *
 * Запуск:  node tests/js/smoke.js <адрес ядра> <токен> <каталог webui>
 * Итог:    JSON в stdout — что удалось проверить и что нет.
 *
 * Так ловятся опечатки в именах элементов и полей API, которые в песочнице
 * некому увидеть: браузера здесь нет.
 */

"use strict";

const path = require("path");
const { Document, makeEvent } = require("./dom.js");

const [base, token, webuiDirArg] = process.argv.slice(2);
const webuiDir = path.resolve(webuiDirArg);
const fs = require("fs");

// настоящий сетевой fetch сохраняем до подмены
const realFetch = global.fetch;

const html = fs.readFileSync(path.join(webuiDir, "index.html"), "utf8");
const document = new Document(html);
const elements = {
  login: null,
  fetchCalls: [],
};

// ------------------------------------------------------------- подстановки
global.document = document;
global.window = {
  location: { href: `${base}/ui/?token=${token}` },
  history: { replaceState() {} },
  matchMedia: () => ({ matches: true, addEventListener() {}, addListener() {} }),
  addEventListener() {},
};
global.sessionStorage = {
  store: {},
  getItem(key) { return this.store[key] || null; },
  setItem(key, value) { this.store[key] = String(value); },
  removeItem(key) { delete this.store[key]; },
};
// navigator в node только для чтения — подменяем свойство, если получится
try {
  Object.defineProperty(global, "navigator", {
    value: { clipboard: { writeText: async (text) => { elements.copied = text; } } },
    configurable: true,
    writable: true,
  });
} catch (error) {
  console.error("navigator не подменить:", error.message);
}
global.location = global.window.location;

function absolute(url) {
  return String(url).startsWith("http") ? url : base + String(url);
}

global.fetch = async (url, options) => {
  const options2 = options || {};
  elements.fetchCalls.push(String(url));
  return fetchReal(absolute(url), options2);
};

// запросы уходят в настоящий сервер ядра
const fetchReal = (url, options) => {
  const setup = { method: options.method || "GET", headers: options.headers || {} };
  if (options.body) setup.body = options.body;
  return realFetch(url, setup);
};

function tick(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function textOf(id) {
  const node = document.getElementById(id);
  return node ? node.textContent : "";
}

function rows(selector) {
  return document.querySelectorAll(selector);
}

async function main() {
  const report = { checks: [], warnings: [] };
  const check = (name, ok, detail) => {
    report.checks.push({ name, ok: Boolean(ok), detail: detail === undefined ? "" : String(detail) });
  };

  // загружаем страницу (модуль сам себя запускает, если есть document)
  require(path.join(webuiDir, "app.js"));
  await tick(300);

  check("страница запустилась", true);
  check("окно подтверждения не висит само по себе",
        document.getElementById("modal").hidden === true,
        `hidden=${document.getElementById("modal").hidden}`);

  // --- токен ---------------------------------------------------------------
  check("токен убран из адреса", global.sessionStorage.getItem("jarvis-token") === token,
        global.sessionStorage.getItem("jarvis-token"));

  // --- чат -----------------------------------------------------------------
  const feed = textOf("chat-feed");
  check("приветствие в чате", feed.includes("Здравствуйте"), feed.slice(0, 60));
  check("история подгружена", elements.fetchCalls.some((call) => call.includes("/history")));

  // --- связь с ядром -------------------------------------------------------
  check("состояние получено", document.getElementById("status-label").textContent.length > 0,
        document.getElementById("status-label").textContent);
  check("чипы провайдеров построены", textOf("providers").length > 0, textOf("providers").slice(0, 80));

  // --- навыки --------------------------------------------------------------
  const tabs = document.querySelectorAll(".tab");
  const skillsTab = tabs.find((tab) => tab.dataset.view === "skills");
  skillsTab.click();
  await tick(400);
  const cards = rows("#skills .card");
  check("раздел «Навыки» открылся", document.getElementById("view-skills").classList.contains("is-active"));
  check("карточки навыков построены", cards.length > 5, `карточек: ${cards.length}`);
  const toggles = rows("#skills .switch");
  check("переключатели навыков есть", toggles.length > 5, `переключателей: ${toggles.length}`);
  check("примеры фраз показаны", rows("#skills .skill-examples").length > 5,
        `строк с примерами: ${rows("#skills .skill-examples").length}`);
  check("переключатель сообщает состояние", toggles.every((node) => ["true", "false"].includes(node.getAttribute("aria-checked"))));

  if (toggles.length) {
    const first = toggles[0];
    const wasOn = first.getAttribute("aria-checked") === "true";
    first.click();
    await tick(900);
    const cards2 = rows("#skills .card");
    const same = cards2.find((card) => card.children
      && card.children.some((child) => child.className === "skill-main"));
    const now = rows("#skills .switch")[0];
    check("навык переключается", now && (now.getAttribute("aria-checked") === "true") !== wasOn,
          `${wasOn} -> ${now && now.getAttribute("aria-checked")}`);
    check("подпись «сколько включено» обновилась", textOf("skills-total").length > 0, textOf("skills-total"));
    if (now) {   // возвращаем как было
      now.click();
      await tick(700);
    }
  }

  // --- настройки -----------------------------------------------------------
  const settingsTab = tabs.find((tab) => tab.dataset.view === "settings");
  settingsTab.click();
  await tick(500);
  const settingRows = rows("#settings .setting-row");
  check("строки настроек построены", settingRows.length >= 10, `строк: ${settingRows.length}`);
  const secret = document.getElementById("set-llm.api_key");
  check("поле ключа скрыто", secret && secret.type === "password");
  const emptyAfterLoad = secret ? secret.value === "" : false;
  check("значение ключа не подставляется в поле", emptyAfterLoad);

  // тема применяется сразу
  const optionValues = (select) => (select.children || []).map((child) => child.value);
  const themeSelect = rows("#settings select").find((node) => optionValues(node).includes("light"));
  if (themeSelect) {
    themeSelect.value = "light";
    themeSelect.dispatchEvent(makeEvent("change", { target: themeSelect }));
    check("тема переключается на лету", document.documentElement.dataset.theme === "light",
          document.documentElement.dataset.theme);
    themeSelect.value = "system";
    themeSelect.dispatchEvent(makeEvent("change", { target: themeSelect }));
  } else {
    report.warnings.push("селектор темы не найден");
  }

  // сохранение изменённой настройки
  const model = document.getElementById("set-llm.model");
  if (model) {
    model.value = "test-model-clock";
    const save = document.getElementById("save");
    save.click();
    await tick(600);
    check("сохранение настроек отчитывается", /Сохранено|Изменений нет/.test(textOf("save-status")),
          textOf("save-status"));
  } else {
    report.warnings.push("поле модели не найдено");
  }

  // --- журнал --------------------------------------------------------------
  const journalTab = tabs.find((tab) => tab.dataset.view === "journal");
  journalTab.click();
  await tick(500);
  const logRows = rows("#log .log-line");
  check("записи журнала показаны", logRows.length > 0, `записей: ${logRows.length}`);
  const chip = rows("#log-levels .chip").find((node) => node.dataset.level === "warning");
  if (chip) {
    chip.click();
    await tick(400);
    check("фильтр журнала по уровню работает", rows("#log .log-line").length <= logRows.length);
  } else {
    report.warnings.push("чип уровня не найден");
  }
  const copyButton = document.getElementById("log-copy");
  if (copyButton) {
    copyButton.click();
    await tick(500);
    check("отчёт для письма получен", typeof elements.copied === "string" && elements.copied.length > 0,
          `символов: ${(elements.copied || "").length}`);
  }

  // --- о программе ---------------------------------------------------------
  const aboutTab = tabs.find((tab) => tab.dataset.view === "about");
  aboutTab.click();
  await tick(400);
  check("раздел «О программе» заполнен", textOf("about").includes("Jarvis") || textOf("about").length > 40,
        textOf("about").slice(0, 60));
  const stopButton = document.getElementById("stop-jarvis");
  check("кнопка остановки на месте", Boolean(stopButton) && stopButton.textContent.includes("Остановить"),
        stopButton ? stopButton.textContent : "нет");
  check("в лицензиях нет трея", !textOf("about").includes("pystray"), textOf("about").slice(0, 40));

  // --- чат: отправка команды ----------------------------------------------
  const chatTab = tabs.find((tab) => tab.dataset.view === "chat");
  chatTab.click();
  await tick(200);
  const input = document.getElementById("input");
  const composer = document.getElementById("composer");
  input.value = "сколько времени";
  composer.dispatchEvent(makeEvent("submit", { target: composer }));
  await tick(2500);
  const after = textOf("chat-feed");
  check("ответ на команду пришёл", /Сейчас|врем/i.test(after) || after.includes("Не понял"),
        after.slice(-160));
  check("ввод очищен после отправки", input.value === "", `«${input.value}»`);
  // --- ответ не должен двоиться: опрос истории не рисует его второй раз ----
  await tick(3600);  // ждём опрос истории (каждые 3 с)
  const bubbles = rows("#chat-feed .bubble").map((node) => node.textContent.trim());
  const duplicates = bubbles.filter((text, index) => text && bubbles.indexOf(text) !== index);
  check("ответ в чате показан один раз", duplicates.length === 0,
        `повторы: ${JSON.stringify(duplicates.slice(0, 3))}`);

  check("в чате нет внутренних ошибок", !/не ответило|is not a function|undefined/i.test(after),
        (after.match(/[^.]*(?:не ответило|is not a function|undefined)[^.]*/i) || [""])[0].slice(0, 120));

  // --- подтверждение опасного действия ------------------------------------
  const before = textOf("chat-feed").length;
  input.value = "перезагрузи компьютер";
  composer.dispatchEvent(makeEvent("submit", { target: composer }));
  await tick(2500);
  const modal = document.getElementById("modal");
  check("окно подтверждения появилось", modal && modal.hidden === false, `hidden=${modal && modal.hidden}`);
  if (modal && modal.hidden === false) {
    check("вопрос показан", textOf("modal-text").length > 0, textOf("modal-text"));
    check("видно, о чём спрашивают", textOf("modal-request").includes("перезагрузи"),
          textOf("modal-request"));
    const no = document.getElementById("modal-no");
    no.click();
    await tick(800);
    check("отказ закрывает окно", modal.hidden === true);
  }
  check("чат не потерял сообщения", textOf("chat-feed").length >= before);

  // --- остановка ассистента из окна (последняя проверка: ядро выключится) ---
  aboutTab.click();
  await tick(300);
  const stop = document.getElementById("stop-jarvis");
  if (stop) {
    stop.click();
    await tick(300);
    const modal2 = document.getElementById("modal");
    check("остановка спрашивает подтверждение", modal2 && modal2.hidden === false
      && textOf("modal-text").includes("Остановить"), textOf("modal-text").slice(0, 60));
    document.getElementById("modal-yes").click();
    await tick(1200);
    check("ядро остановлено по кнопке", document.body.dataset.connected === "false",
          `connected=${document.body.dataset.connected}`);
  } else {
    report.warnings.push("кнопка остановки не найдена");
  }

  report.calls = elements.fetchCalls.length;
  console.log(JSON.stringify(report));
  process.exit(0);
}

main().catch((error) => {
  console.log(JSON.stringify({ error: String(error && error.stack ? error.stack : error) }));
  process.exit(1);
});
