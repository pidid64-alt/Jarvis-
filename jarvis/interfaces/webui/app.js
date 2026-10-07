/* Логика окна Jarvis в браузере.
 *
 * Правило прежнее: **в интерфейсе нет логики ассистента**. Страница только
 * показывает то, что отдаёт ядро, и отправляет ему ввод — всё через тот же
 * локальный API, что раньше использовало окно на Tkinter.
 *
 * Файл состоит из двух частей:
 *   1. чистые функции (их проверяет `tests/test_webui.py` через node);
 *   2. работа с окном (запускается только в браузере).
 */

"use strict";

// ------------------------------------------------------------------ подписи

const LABELS = {
  "state.idle": "Ожидание",
  "state.listening": "Слушаю",
  "state.thinking": "Думаю",
  "state.speaking": "Отвечаю",
  "state.waiting_confirmation": "Жду подтверждения",
  "state.error": "Ошибка",
  "state.ready": "Готов слушать",
  "state.not_ready": "Не готов",
  "provider.llm": "Модель",
  "provider.stt": "Распознавание",
  "provider.tts": "Голос",
  "chat.greeting": "Здравствуйте. Напишите вопрос или нажмите «Слушать».",
  "chat.thinking": "Думаю…",
  "chat.stopped": "Перестал ждать ответ. Ответ появится в истории, когда будет готов.",
  "theme.system": "как в системе",
  "theme.dark": "тёмная",
  "theme.light": "светлая",
  "skills.on": "включён",
  "skills.off": "выключен",
  "skills.builtin": "встроенный",
  "skills.migrated": "перенесён из старой версии",
  "skills.rights": "Права",
  "skills.confirm": "спросит подтверждение",
  "skills.total": "Навыков: {total} (включено {enabled})",
  "skills.pick": "Навык не выбран",
  "settings.saved": "Сохранено настроек: {n}",
  "settings.unchanged": "Изменений нет",
  "settings.saving": "Сохраняю…",
  "settings.failed": "Не сохранилось: {reason}",
  "settings.key_saved": "Ключ сохранён: {masked}",
  "settings.key_empty": "Введите ключ",
  "settings.key_none": "ключ не задан",
  "settings.key_set": "сохранён: {masked}",
  "settings.save_key": "Сохранить ключ",
  "permissions.restricted": "ограниченный (безопасный)",
  "permissions.normal": "обычный",
  "journal.copied": "Сведения для отчёта скопированы (секреты скрыты)",
  "journal.copy_failed": "Не удалось получить сведения: {reason}",
  "journal.empty": "Записей нет",
  "confirm.default": "Выполнить действие? Разрешите, если это то, что вы просили.",
  "confirm.request": "Запрос: «{text}»",
  "error.core": "Ядро не ответило: {reason}",
  "about.copied": "Сведения о программе скопированы",
  "about.stop": "Остановить Jarvis",
  "about.stop_hint": "Ядро выключится, окно перестанет отвечать. Запустить снова: jarvis gui",
  "about.stop_question": "Остановить Jarvis? Ассистент выключится, окно перестанет отвечать.",
  "about.stop_failed": "Ядро не подтвердило остановку: {reason}",
};

/** Тексты с подстановками: `t("skills.total", {total: 3})`. */
function t(key, values) {
  let text = LABELS[key] || key;
  for (const [name, value] of Object.entries(values || {})) {
    text = text.replace(`{${name}}`, String(value));
  }
  return text;
}

// ------------------------------------------------------- чистые функции

/** Текст окна подтверждения: пустой вопрос заменяем понятным пояснением. */
function confirmationText(question) {
  const text = String(question == null ? "" : question).trim();
  return text || LABELS["confirm.default"];
}

/** Имя ключа из ссылки: `${JARVIS_LLM_KEY}` -> `JARVIS_LLM_KEY`. */
function secretName(reference, fallback) {
  const match = /\$\{([A-Za-z_][A-Za-z0-9_]*)\}/.exec(String(reference || ""));
  return match ? match[1] : fallback || "";
}

/** Тема из настройки: `system` превращается в dark/light по системе. */
function themeName(setting, prefersDark) {
  const value = String(setting || "").trim().toLowerCase();
  if (value === "dark" || value === "light") return value;
  return prefersDark ? "dark" : "light";
}

/** Цвет уровня для строки журнала: info / warning / error. */
function levelOf(level) {
  const value = String(level || "info").trim().toLowerCase();
  if (value.startsWith("warn")) return "warning";
  if (value.startsWith("err") || value.startsWith("crit") || value.startsWith("fatal")) return "error";
  return "info";
}

/** Плашка подходит пользователю (справа) или ассистенту (слева). */
function bubbleClass(role, ok) {
  if (role === "user") return "bubble user";
  if (ok === false) return "bubble error";
  return "bubble assistant";
}

/** Провайдеры одной строкой для подписи в шапке. */
function providerChips(status) {
  const providers = (status && status.providers) || {};
  return ["llm", "stt", "tts"].map((name) => {
    const state = providers[name] || {};
    const ready = Boolean(state.available);
    return {
      name,
      label: `${t(`provider.${name}`)}: ${t(ready ? "state.ready" : "state.not_ready")}`,
      ok: ready,
      reason: state.reason || "",
    };
  });
}

/** Разбор строк журнала из записей API в строки таблицы. */
function journalRows(entries) {
  return (entries || []).map((entry) => ({
    level: levelOf(entry.level),
    time: new Date(Number(entry.ts || 0) * 1000).toTimeString().slice(0, 8),
    source: entry.source || "?",
    message: entry.message || "",
  }));
}

/** Число из поля ввода: пустое или мусор — `fallback`. */
function numberValue(raw, fallback) {
  if (raw === "" || raw === null || raw === undefined) return fallback;
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

/** Что изменилось в настройках: пишем только изменённое. */
function changedSettings(values, original) {
  const changes = [];
  for (const [key, value] of Object.entries(values || {})) {
    if (value !== original[key]) changes.push([key, value]);
  }
  return changes;
}

const PURE = { LABELS, t, confirmationText, numberValue, secretName, themeName, levelOf, bubbleClass, providerChips, journalRows, changedSettings };

// ------------------------------------------------------------------ страница

if (typeof document !== "undefined") {
  const Api = {
    // токен берём из хранилища при каждом запросе: при первом открытии он
    // приходит в адресе, и его ещё нет ни в хранилище, ни в поле объекта
    get token() {
      return sessionStorage.getItem("jarvis-token") || "";
    },
    base: "",

    readToken() {
      const url = new URL(window.location.href);
      const fromUrl = url.searchParams.get("token") || "";
      if (fromUrl) {
        sessionStorage.setItem("jarvis-token", fromUrl);
        url.searchParams.delete("token");        // токен не оставляем в адресной строке
        window.history.replaceState({}, "", url.pathname + url.search + url.hash);
      }
    },

    async call(path, { method = "GET", body = null } = {}) {
      const send = (withQuery) => {
        let target = path;
        if (withQuery && this.token) {
          target += (target.includes("?") ? "&" : "?") + `token=${encodeURIComponent(this.token)}`;
        }
        return fetch(target, {
          method,
          headers: {
            "X-Jarvis-Token": this.token,
            ...(body ? { "Content-Type": "application/json" } : {}),
          },
          body: body ? JSON.stringify(body) : undefined,
        });
      };
      let response = await send(false);
      // некоторые прокси срезают нестандартные заголовки — тогда пробуем токен в адресе
      if (response.status === 401) response = await send(true);
      let payload = {};
      try {
        payload = await response.json();
      } catch (error) {
        throw new Error(`ответ не разобрать (${response.status})`);
      }
      if (!response.ok) throw new Error(payload.message || payload.error || `ошибка ${response.status}`);
      return payload;
    },

    get(path) { return this.call(path); },
    post(path, body) { return this.call(path, { method: "POST", body }); },
  };

  const el = (id) => document.getElementById(id);
  const state = {
    busy: false,
    pendingId: null,
    lastEventSeq: 0,
    lastChatTs: 0,
    stateTs: 0,
    config: null,
    original: {},
    skills: [],
    view: "chat",
    stopWaiting: false,
  };

  // ------------------------------------------------------------------ мелочи

  function toast(message, kind = "info") {
    const node = document.createElement("div");
    node.className = `toast ${kind}`;
    node.textContent = message;
    el("toasts").appendChild(node);
    setTimeout(() => node.remove(), 5200);
  }

  function setHint(text) { el("hint").textContent = text; }

  function setState(name, label) {
    const status = el("status");
    status.dataset.state = name || "idle";
    el("status-label").textContent = label || t(`state.${name || "idle"}`);
    state.stateTs = Date.now() / 1000;
  }

  function applyTheme(setting) {
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme = themeName(setting, prefersDark);
  }

  // -------------------------------------------------------------------- чат

  function addBubble(role, text, ok) {
    if (!text) return;
    const node = document.createElement("div");
    node.className = bubbleClass(role, ok);
    node.textContent = text;
    el("chat-feed").appendChild(node);
    const scroller = el("chat-scroll");
    scroller.scrollTop = scroller.scrollHeight;
  }

  function addSystem(text) { addBubble("system", text, true); }

  async function refreshHistory() {
    const { history } = await Api.get("/history?limit=50");
    for (const record of history || []) {
      const ts = Number(record.ts || 0);
      if (ts <= state.lastChatTs) continue;
      state.lastChatTs = ts;
      addBubble(record.role === "user" ? "user" : "assistant", String(record.text || ""), true);
    }
  }

  function renderReply(reply) {
    if (!reply) return;
    const text = reply.text || reply.speech || "";
    if (!text) return;
    if (reply.ok === false && reply.error) addBubble("system", t("error.core", { reason: reply.error }), false);
    else addBubble("assistant", text, true);
    setState(reply.ok === false ? "error" : "idle");
  }

  async function handleAnswer(answer) {
    answer = answer || {};
    if (answer.state === "confirmation_required") return askConfirmation(answer);
    if (answer.state === "done") { setBusy(false); renderReply(answer.reply); return refreshHistory(); }
    if (answer.state === "working" || answer.state === "timeout") return watchTask(answer.pending_id || state.pendingId);
    setBusy(false);
    setState("idle");
  }

  function askConfirmation(answer) {
    const pendingId = answer.pending_id || state.pendingId;
    // не открываем второе окно для того же запроса: иначе вопрос возвращается
    // сам собой и кажется, что окно «залипло»
    if (!el("modal").hidden && pendingId && pendingId === state.pendingId) return;
    state.pendingId = pendingId;
    setState("waiting_confirmation");
    openModal(answer.question, answer.request, async (approved) => {
      setState("thinking");
      try {
        const result = await Api.post("/confirm", { pending_id: state.pendingId, approved });
        await handleAnswer(result);
      } catch (error) {
        fail(error);
      }
    });
  }

  async function watchTask(pendingId) {
    if (!pendingId) { setBusy(false); setState("idle"); return; }
    state.pendingId = pendingId;
    state.stopWaiting = false;
    while (!state.stopWaiting) {
      await new Promise((resolve) => setTimeout(resolve, 300));
      if (state.stopWaiting) break;
      let answer;
      try {
        answer = await Api.get(`/ask/result?pending_id=${encodeURIComponent(pendingId)}`);
      } catch (error) {
        if (String(error.message).includes("не найден")) break;   // запрос потерялся — не беда
        fail(error);
        return;
      }
      if (answer.state === "done") { setBusy(false); renderReply(answer.reply); return refreshHistory(); }
      if (answer.state === "confirmation_required") { setBusy(false); return askConfirmation(answer); }
    }
    setBusy(false);
    setState("idle");
  }

  function setBusy(busy) {
    state.busy = busy;
    el("send").disabled = busy;
    el("mic").disabled = busy;
    el("stop").hidden = !busy;
  }

  function fail(error) {
    setBusy(false);
    setState("error");
    addSystem(t("error.core", { reason: error.message || error }));
    toast(t("error.core", { reason: error.message || error }), "error");
  }

  async function send(text) {
    if (state.busy) return;
    const speak = el("speak").getAttribute("aria-pressed") === "true";
    setBusy(true);
    setState("thinking");
    try {
      const answer = await Api.post("/ask", { text, speak });
      await handleAnswer(answer);
    } catch (error) {
      fail(error);
    }
  }

  async function listen() {
    if (state.busy) return;
    const speak = el("speak").getAttribute("aria-pressed") === "true";
    setBusy(true);
    setState("listening");
    try {
      const answer = await Api.post("/voice", { speak });
      await handleAnswer(answer);
    } catch (error) {
      fail(error);
    }
  }

  // ------------------------------------------------------------------ навыки

  const PERMISSION_LABELS = {
    shell: "системные команды", files: "файлы", clipboard: "буфер обмена",
    screen: "снимок экрана", network: "сеть", notify: "уведомления",
    dangerous: "опасные действия",
  };

  function permissionChips(skill) {
    const permissions = skill.permissions || {};
    const chips = [];
    for (const [key, value] of Object.entries(permissions)) {
      if (!value) continue;
      chips.push(PERMISSION_LABELS[key] || key);
    }
    if (skill.confirm || permissions.dangerous) chips.push(t("skills.confirm"));
    return chips.length ? chips : ["только ответы"];
  }

  function renderSkills() {
    const box = el("skills");
    box.textContent = "";
    for (const skill of state.skills) {
      const card = document.createElement("article");
      card.className = `card skill-card${skill.enabled ? "" : " is-off"}`;

      const main = document.createElement("div");
      main.className = "skill-main";
      const title = document.createElement("div");
      title.className = "skill-title";
      const name = document.createElement("b");
      name.textContent = skill.name || skill.id;
      title.appendChild(name);
      const origin = document.createElement("span");
      origin.className = "chip";
      origin.textContent = skill.builtin ? t("skills.builtin") : t("skills.migrated");
      title.appendChild(origin);
      main.appendChild(title);

      const desc = document.createElement("p");
      desc.className = "skill-desc";
      desc.textContent = skill.description || "";
      main.appendChild(desc);

      const examples = (skill.examples || []).slice(0, 4);
      if (examples.length) {
        const line = document.createElement("p");
        line.className = "skill-examples muted";
        line.textContent = examples.map((phrase) => `«${phrase}»`).join(" · ");
        main.appendChild(line);
      }

      const chips = document.createElement("div");
      chips.className = "skill-chips";
      for (const label of permissionChips(skill)) {
        const chip = document.createElement("span");
        chip.className = "chip";
        chip.textContent = label;
        chips.appendChild(chip);
      }
      const actions = document.createElement("span");
      actions.className = "skill-actions muted";
      actions.textContent = `действий: ${(skill.actions || []).length}`;
      chips.appendChild(actions);
      main.appendChild(chips);

      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "switch";
      toggle.setAttribute("role", "switch");
      toggle.setAttribute("aria-checked", skill.enabled ? "true" : "false");
      toggle.setAttribute("aria-label", `${skill.name}: ${skill.enabled ? t("skills.on") : t("skills.off")}`);
      toggle.addEventListener("click", () => toggleSkill(skill, !skill.enabled));

      card.append(main, toggle);
      box.appendChild(card);
    }
    const enabled = state.skills.filter((item) => item.enabled).length;
    el("skills-total").textContent = t("skills.total", { total: state.skills.length, enabled });
  }

  async function loadSkills() {
    try {
      const { skills } = await Api.get("/skills");
      state.skills = (skills || []).slice().sort((a, b) =>
        (b.enabled - a.enabled) || String(a.name).localeCompare(String(b.name), "ru"));
      renderSkills();
    } catch (error) { fail(error); }
  }

  async function toggleSkill(skill, enabled) {
    try {
      await Api.post("/skills/toggle", { id: skill.id, enabled });
      skill.enabled = enabled;
      renderSkills();
    } catch (error) { fail(error); }
  }

  // --------------------------------------------------------------- настройки

  const SETTINGS_SCHEMA = [
    {
      title: "Модель и поиск",
      rows: [
        { key: "llm.model", label: "Модель", kind: "text", note: "например gpt-4o-mini" },
        { key: "llm.base_url", label: "Адрес сервиса", kind: "text", note: "OpenAI-совместимый, обычно .../v1" },
        { key: "llm.enabled", label: "Пользоваться моделью", kind: "bool" },
        { key: "llm.timeout_seconds", label: "Ожидание ответа модели, с", kind: "number",
          note: "локальной модели нужно 30–120 с" },
        { key: "llm.api_key", label: "Ключ модели", kind: "secret" },
      ],
    },
    {
      title: "Голос",
      rows: [
        { key: "voice.enabled", label: "Отвечать вслух", kind: "bool" },
        { key: "voice.wakeword.enabled", label: "Слушать слово-активатор", kind: "bool" },
        { key: "tts.voice", label: "Голос синтеза", kind: "text", note: "например ru_RU-irina-medium" },
        { key: "assistant.language", label: "Язык интерфейса", kind: "choice" },
      ],
    },
    {
      title: "Горячая клавиша",
      rows: [
        { key: "hotkey.spec", label: "Сочетание", kind: "text", note: "например Super+J" },
        { key: "hotkey.enabled", label: "Включена", kind: "bool" },
      ],
    },
    {
      title: "Интерфейс",
      rows: [
        { key: "assistant.theme", label: "Тема", kind: "theme", note: "по умолчанию — как в системе" },
      ],
    },
    {
      title: "Права",
      rows: [
        { key: "permissions.mode", label: "Режим", kind: "choice" },
      ],
    },
  ];

  function dig(tree, dotted) {
    return dotted.split(".").reduce((node, part) => (node == null ? undefined : node[part]), tree);
  }

  function renderSettings() {
    const tree = (state.config && state.config.config) || {};
    const languages = (state.config && state.config.languages) || ["ru"];
    const box = el("settings");
    box.textContent = "";
    state.original = {};

    for (const section of SETTINGS_SCHEMA) {
      const card = document.createElement("section");
      card.className = "card section";
      const title = document.createElement("div");
      title.className = "section-title";
      title.textContent = section.title;
      card.appendChild(title);

      for (const row of section.rows) {
        const line = document.createElement("div");
        line.className = "setting-row";
        const label = document.createElement("label");
        label.className = "setting-label";
        label.textContent = row.label;
        line.appendChild(label);

        const control = document.createElement("div");
        control.className = "setting-control";
        buildControl(row, tree, languages, control, label);
        line.appendChild(control);
        card.appendChild(line);
      }
      box.appendChild(card);
    }
    el("settings-path").textContent = (state.config && state.config.path) || "…";
  }

  function buildControl(row, tree, languages, control, label) {
    const secret = row.kind === "secret";
    const value = secret ? "" : dig(tree, row.key);
    // имя ключа берём из ссылки в настройках: ${JARVIS_LLM_KEY} -> JARVIS_LLM_KEY
    const name = secret ? secretName(dig(tree, row.key), "JARVIS_LLM_KEY") : "";
    const envEntry = ((state.config.env || {}).names || []).find((item) => item.name === name) || {};

    if (row.kind === "bool") {
      const input = document.createElement("input");
      input.type = "checkbox";
      input.id = `set-${row.key}`;
      input.checked = Boolean(value);
      label.htmlFor = input.id;
      state.original[row.key] = Boolean(value);
      control.appendChild(input);
      const hint = document.createElement("span");
      hint.className = "muted";
      hint.textContent = row.note || "";
      control.appendChild(hint);
      return;
    }

    if (row.kind === "number") {
      const input = document.createElement("input");
      input.type = "number";
      input.id = `set-${row.key}`;
      input.min = "1";
      input.max = "600";
      input.step = "5";
      input.value = String(numberValue(value, ""));
      label.htmlFor = input.id;
      state.original[row.key] = numberValue(value, null);
      control.appendChild(input);
      if (row.note) {
        const hint = document.createElement("span");
        hint.className = "muted";
        hint.textContent = row.note;
        control.appendChild(hint);
      }
      return;
    }

    if (row.kind === "theme") {
      const select = document.createElement("select");
      for (const name of ["system", "dark", "light"]) {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = t(`theme.${name}`);
        option.selected = String(value || "system") === name;
        select.appendChild(option);
      }
      state.original[row.key] = String(value || "system");
      select.addEventListener("change", () => applyTheme(select.value));
      control.appendChild(select);
      return;
    }

    if (row.kind === "choice") {
      const select = document.createElement("select");
      const options = row.key === "assistant.language" ? languages : ["restricted", "normal"];
      const labels = { restricted: t("permissions.restricted"), normal: t("permissions.normal") };
      for (const name of options) {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = labels[name] || name;
        option.selected = String(value) === name;
        select.appendChild(option);
      }
      state.original[row.key] = String(value || "");
      control.appendChild(select);
      return;
    }

    const input = document.createElement("input");
    input.type = secret ? "password" : "text";
    input.id = `set-${row.key}`;
    input.value = secret ? "" : String(value ?? "");
    input.placeholder = secret
      ? (envEntry.set ? t("settings.key_set", { masked: envEntry.masked }) : t("settings.key_none"))
      : "";
    input.autocomplete = "off";
    input.spellcheck = false;
    if (!secret) state.original[row.key] = String(value ?? "");
    label.htmlFor = input.id;
    control.appendChild(input);

    if (secret) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn";
      button.textContent = t("settings.save_key");
      button.addEventListener("click", async () => {
        const secretValue = input.value.trim();
        if (!secretValue) return toast(t("settings.key_empty"), "error");
        try {
          const result = await Api.post("/secret", { name, value: secretValue });
          input.value = "";
          input.placeholder = t("settings.key_set", { masked: result.masked || "…" });
          toast(t("settings.key_saved", { masked: result.masked || "…" }));
          await loadConfig();
        } catch (error) { fail(error); }
      });
      control.appendChild(button);
    } else if (row.note) {
      const hint = document.createElement("span");
      hint.className = "muted";
      hint.textContent = row.note;
      control.appendChild(hint);
    }
  }

  function collectSettings() {
    const values = {};
    for (const section of SETTINGS_SCHEMA) {
      for (const row of section.rows) {
        if (row.kind === "secret") continue;
        const node = document.getElementById(`set-${row.key}`);
        if (!node) continue;
        if (row.kind === "bool") values[row.key] = node.checked;
        else if (row.kind === "number") values[row.key] = numberValue(node.value, null);
        else values[row.key] = node.value;
      }
    }
    return values;
  }

  async function saveSettings() {
    const changes = changedSettings(collectSettings(), state.original);
    if (!changes.length) { el("save-status").textContent = t("settings.unchanged"); return; }
    el("save-status").textContent = t("settings.saving");
    let applied = 0;
    const failed = [];
    for (const [key, value] of changes) {
      try {
        await Api.post("/config", { key, value });
        state.original[key] = value;
        applied += 1;
      } catch (error) { failed.push(`${key}: ${error.message}`); }
    }
    if (failed.length) el("save-status").textContent = t("settings.failed", { reason: failed.slice(0, 2).join("; ") });
    else el("save-status").textContent = t("settings.saved", { n: applied });
    await loadConfig();
  }

  async function loadConfig() {
    try {
      state.config = await Api.get("/config");
      const languages = await Api.get("/languages");
      state.config.languages = languages.languages || ["ru"];
      renderSettings();
    } catch (error) { fail(error); }
  }

  // ------------------------------------------------------------------ журнал

  async function loadJournal() {
    const active = document.querySelector("#log-levels .chip.is-active");
    const level = active ? active.dataset.level : "all";
    const search = el("log-search").value.trim();
    const query = new URLSearchParams({ limit: "300" });
    if (level !== "all") query.set("level", level === "warning" ? "warning" : level);
    if (search) query.set("search", search);
    try {
      const { entries } = await Api.get(`/journal?${query}`);
      const rows = journalRows(entries);
      const box = el("log");
      box.textContent = "";
      if (!rows.length) {
        const empty = document.createElement("div");
        empty.className = "log-line info";
        empty.innerHTML = `<span class="log-badge">INFO</span><span class="msg">${t("journal.empty")}</span>`;
        box.appendChild(empty);
      }
      for (const row of rows) {
        const line = document.createElement("div");
        line.className = `log-line ${row.level}`;
        const badge = document.createElement("span");
        badge.className = "log-badge";
        badge.textContent = row.level === "info" ? "INFO" : row.level === "warning" ? "WARN" : "ERROR";
        const time = document.createElement("span");
        time.className = "time";
        time.textContent = row.time;
        const source = document.createElement("span");
        source.className = "src";
        source.textContent = row.source;
        const message = document.createElement("span");
        message.className = "msg";
        message.textContent = row.message;
        line.append(badge, time, source, message);
        box.appendChild(line);
      }
      el("log-hint").textContent = `показано записей: ${rows.length}`;
    } catch (error) { fail(error); }
  }

  async function copyReport() {
    try {
      const { report } = await Api.get("/journal/report?limit=300");
      await navigator.clipboard.writeText(report);
      toast(t("journal.copied"));
    } catch (error) {
      toast(t("journal.copy_failed", { reason: error.message || error }), "error");
    }
  }

  // -------------------------------------------------------------- о программе

  function renderAbout(info) {
    const box = el("about");
    box.textContent = "";
    const card = document.createElement("section");
    card.className = "card section";
    const rows = [
      ["Программа", `Jarvis ${info.version || ""}`],
      ["Ядро", (info.api && info.api.url) || ""],
      ["Настройки", (info.status && info.status.config_path) || ""],
      ["Данные", (info.status && info.status.state_dir) || ""],
      ["Язык и права", `${info.status ? info.status.language : ""}, режим ${info.status ? info.status.permissions_mode : ""}`],
    ];
    for (const [label, value] of rows) {
      const line = document.createElement("div");
      line.className = "setting-row";
      const left = document.createElement("span");
      left.className = "setting-label";
      left.textContent = label;
      const right = document.createElement("span");
      right.className = "setting-control";
      const code = document.createElement("code");
      code.textContent = value;
      right.appendChild(code);
      line.append(left, right);
      card.appendChild(line);
    }
    const licenses = document.createElement("div");
    licenses.className = "section";
    licenses.innerHTML = `<div class="section-title">Использованные проекты и лицензии</div>
      <p class="muted">Piper (MIT) — синтез речи · whisper.cpp (MIT) — распознавание ·
      openWakeWord (Apache-2.0) — слово-активатор. Окно показывает браузер, который уже
      стоит в системе; страница Jarvis написана здесь и сторонних библиотек не использует.
      Шрифты: Segoe UI (Windows), Noto Sans и DejaVu Sans (SIL OFL / свободная лицензия).
      Значки интерфейса нарисованы в этом проекте.</p>`;
    card.appendChild(licenses);

    const actions = document.createElement("div");
    actions.className = "row-actions";
    actions.style.padding = "0 var(--space-l) var(--space-l)";
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "btn";
    copy.textContent = "Скопировать сведения";
    copy.addEventListener("click", async () => {
      await navigator.clipboard.writeText(
        `Jarvis ${info.version}\nЯдро: ${(info.api || {}).url}\nНастройки: ${(info.status || {}).config_path}\n` +
        `Данные: ${(info.status || {}).state_dir}\nНавыков: ${((info.status || {}).skills || {}).total}`);
      toast(t("about.copied"));
    });
    const stop = document.createElement("button");
    stop.type = "button";
    stop.className = "btn danger";
    stop.id = "stop-jarvis";
    stop.textContent = t("about.stop");
    stop.addEventListener("click", () => {
      openModal(t("about.stop_question"), "", async (approved) => {
        if (!approved) return;
        try {
          const answer = await Api.post("/shutdown", {});
          if (answer && answer.stopping === false) {
            toast(t("about.stop_failed", { reason: "нет обработчика остановки" }), "error");
            return;
          }
          toast(t("about.stop"));
          el("conn").textContent = "ядро остановлено";
          document.body.dataset.connected = "false";
        } catch (error) {
          // ядро могло закрыть соединение, уже выключаясь — это не ошибка
          toast(t("about.stop"));
          document.body.dataset.connected = "false";
        }
      });
    });
    actions.append(copy, stop);
    card.appendChild(actions);

    const hint = document.createElement("p");
    hint.className = "muted";
    hint.style.padding = "0 var(--space-l) var(--space-l)";
    hint.textContent = t("about.stop_hint");
    card.appendChild(hint);
    box.appendChild(card);
  }

  // ------------------------------------------------------------------ модалка

  let modalResolver = null;

  function openModal(question, request, onAnswer) {
    el("modal-text").textContent = confirmationText(question);
    const line = el("modal-request");
    line.textContent = request ? t("confirm.request", { text: request }) : "";
    line.hidden = !request;
    el("modal").hidden = false;
    modalResolver = onAnswer;
    el("modal-yes").focus();
  }

  function closeModal(approved) {
    el("modal").hidden = true;
    const resolver = modalResolver;
    modalResolver = null;
    if (resolver) resolver(approved);
  }

  // -------------------------------------------------------------------- вьюхи

  function showView(name) {
    state.view = name;
    for (const tab of document.querySelectorAll(".tab")) {
      tab.classList.toggle("is-active", tab.dataset.view === name);
    }
    for (const view of document.querySelectorAll(".view")) {
      view.classList.toggle("is-active", view.id === `view-${name}`);
    }
    if (name === "skills") loadSkills();
    if (name === "journal") loadJournal();
    if (name === "settings") loadConfig();
    if (name === "about") loadAbout();
  }

  async function loadAbout() {
    try {
      const info = await Api.get("/health");
      renderAbout(info);
    } catch (error) { fail(error); }
  }

  // ----------------------------------------------------------------- опрос ядра

  async function pollStatus() {
    try {
      const { status } = await Api.get("/status");
      el("providers").textContent = "";
      for (const chip of providerChips(status)) {
        const node = document.createElement("span");
        node.className = `chip ${chip.ok ? "is-ok" : "is-bad"}`;
        if (chip.ok) {
          const dot = document.createElement("span");
          dot.className = "dot";
          node.appendChild(dot);
        }
        node.appendChild(document.createTextNode(chip.label));
        if (chip.reason && !chip.ok) node.title = chip.reason;
        el("providers").appendChild(node);
      }
      el("conn").textContent = "ядро на связи";
      document.body.dataset.connected = "true";
    } catch (error) {
      el("conn").textContent = "ядро не отвечает";
      document.body.dataset.connected = "false";
    }
  }

  async function pollEvents() {
    try {
      const { events } = await Api.get(`/events?since=${state.lastEventSeq}`);
      for (const event of events || []) {
        state.lastEventSeq = Math.max(state.lastEventSeq, Number(event.seq || 0));
        if (event.type !== "state") continue;
        if (Number(event.ts || 0) <= state.stateTs) continue;   // старое событие не перебивает свежий ответ
        setState(String(event.state || "idle"));
      }
    } catch (error) {
      /* связь проверим следующим опросом */
    } finally {
      setTimeout(pollEvents, 500);
    }
  }

  function pollHistory() {
    refreshHistory().catch(() => {}).finally(() => setTimeout(pollHistory, 3000));
  }

  // -------------------------------------------------------------------- старт

  async function boot() {
    Api.readToken();
    applyTheme("system");
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      const select = document.querySelector("#settings select");
      applyTheme(state.config ? dig(state.config.config, "assistant.theme") : "system");
    });

    el("composer").addEventListener("submit", (event) => {
      event.preventDefault();
      const input = el("input");
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      send(text);
    });
    el("mic").addEventListener("click", () => listen());
    el("stop").addEventListener("click", () => {
      state.stopWaiting = true;
      setBusy(false);
      setState("idle");
      addSystem(t("chat.stopped"));
    });
    el("speak").addEventListener("click", () => {
      const button = el("speak");
      const pressed = button.getAttribute("aria-pressed") === "true";
      button.setAttribute("aria-pressed", pressed ? "false" : "true");
    });
    el("tabs").addEventListener("click", (event) => {
      const tab = event.target.closest(".tab");
      if (tab) showView(tab.dataset.view);
    });
    el("modal-no").addEventListener("click", () => closeModal(false));
    el("modal-yes").addEventListener("click", () => closeModal(true));
    el("save").addEventListener("click", () => saveSettings());
    el("skills-refresh").addEventListener("click", () => loadSkills());
    el("log-refresh").addEventListener("click", () => loadJournal());
    el("log-copy").addEventListener("click", () => copyReport());
    el("log-search").addEventListener("input", () => {
      clearTimeout(window.__searchTimer);
      window.__searchTimer = setTimeout(loadJournal, 350);
    });
    el("log-levels").addEventListener("click", (event) => {
      const chip = event.target.closest(".chip");
      if (!chip) return;
      for (const other of document.querySelectorAll("#log-levels .chip")) {
        other.classList.toggle("is-active", other === chip);
      }
      loadJournal();
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !el("modal").hidden) closeModal(false);
      if (event.ctrlKey && (event.key === "l" || event.key === "L" || event.key === "д")) {
        event.preventDefault();
        listen();
      }
      if (event.ctrlKey && event.key === "Enter") {
        event.preventDefault();
        const text = el("input").value.trim();
        if (text) { el("input").value = ""; send(text); }
      }
    });

    addSystem(t("chat.greeting"));
    await pollStatus();
    try {
      await loadConfig();
      applyTheme(dig((state.config.config || {}), "assistant.theme") || "system");
    } catch (error) { /* настройки покажем, когда ядро ответит */ }
    try {
      await refreshHistory();
    } catch (error) { /* история появится при следующем опросе */ }
    setState("idle");

    pollEvents();
    pollHistory();
    setInterval(pollStatus, 5000);
    setHint("Enter — отправить · Ctrl+L — слушать · Esc — закрыть окно подтверждения");
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
}

// Проверки чистых функций идут из node (tests/test_webui.py), браузеру export не нужен
if (typeof module !== "undefined" && module.exports) module.exports = PURE;
