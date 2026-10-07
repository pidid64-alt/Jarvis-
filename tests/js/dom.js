/* Крошечный DOM для проверки страницы окна без браузера.
 *
 * Не настоящий браузер, а «достаточно похоже»: разбор index.html по идентифика-
 * торам, элементы, классы, события и поиск по простым селекторам. Нужен тестам
 * (`tests/test_webui.py`), чтобы прогнать app.js целиком и поймать опечатки.
 */

"use strict";

class ClassList {
  constructor(node) {
    this.node = node;
  }
  get set() {
    return new Set(String(this.node.className || "").split(/\s+/).filter(Boolean));
  }
  values() {
    return [...this.set];
  }
  contains(name) {
    return this.set.has(name);
  }
  add(...names) {
    const set = this.set;
    names.forEach((name) => set.add(name));
    this.node.className = [...set].join(" ");
  }
  remove(...names) {
    const set = this.set;
    names.forEach((name) => set.delete(name));
    this.node.className = [...set].join(" ");
  }
  toggle(name, force) {
    const on = force === undefined ? !this.contains(name) : Boolean(force);
    if (on) this.add(name);
    else this.remove(name);
    return on;
  }
}

class Node {
  constructor(tag) {
    this.tagName = String(tag || "div").toUpperCase();
    this.children = [];
    this.childNodes = this.children;
    this.parentNode = null;
    this.attributes = {};
    this.dataset = {};
    this.style = {};
    this.className = "";
    this.classList = new ClassList(this);
    this._text = "";
    this._listeners = {};
    this.id = "";
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.value = "";
    this.placeholder = "";
    this.type = "";
    this.href = "";
    this.selected = false;
    this.options = [];
  }

  get textContent() {
    if (this.children.length === 0) return this._text;
    return this.children.map((child) => child.textContent).join("");
  }
  set textContent(value) {
    this.children.length = 0;
    this._text = String(value == null ? "" : value);
  }
  get innerHTML() {
    return this._html || "";
  }
  set innerHTML(value) {
    this._html = String(value == null ? "" : value);
    this.children.length = 0;
    this._text = this._html.replace(/<[^>]*>/g, "");   // грубо, но для проверок хватает
  }

  appendChild(child) {
    if (!child) return child;
    if (child.__fragment) {
      child.children.forEach((item) => this.appendChild(item));
      child.children.length = 0;
      return child;
    }
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  append(...nodes) {
    nodes.forEach((node) => {
      if (node == null) return;
      this.appendChild(typeof node === "string" ? textNode(node) : node);
    });
  }
  prepend(...nodes) {
    nodes.reverse().forEach((node) => {
      if (node == null) return;
      const created = typeof node === "string" ? textNode(node) : node;
      created.parentNode = this;
      this.children.unshift(created);
    });
  }
  replaceChildren(...nodes) {
    this.children.length = 0;
    this.append(...nodes);
  }
  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index >= 0) this.children.splice(index, 1);
    return child;
  }
  remove() {
    if (this.parentNode) this.parentNode.removeChild(this);
  }
  contains(other) {
    let node = other;
    while (node) {
      if (node === this) return true;
      node = node.parentNode;
    }
    return false;
  }
  closest(selector) {
    let node = this;
    while (node) {
      if (matches(node, selector)) return node;
      node = node.parentNode;
    }
    return null;
  }
  focus() {
    document.activeElement = this;
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "id") this.id = String(value);
    if (name === "class") this.className = String(value);
    if (name.startsWith("data-")) {
      const key = name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
      this.dataset[key] = String(value);
    }
  }
  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name)
      ? this.attributes[name] : null;
  }
  removeAttribute(name) {
    delete this.attributes[name];
  }
  querySelectorAll(selector) {
    const found = [];
    walk(this, (node) => {
      if (node !== this && matches(node, selector)) found.push(node);
    });
    return found;
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
  addEventListener(type, handler) {
    (this._listeners[type] = this._listeners[type] || []).push(handler);
  }
  removeEventListener(type, handler) {
    const list = this._listeners[type] || [];
    const index = list.indexOf(handler);
    if (index >= 0) list.splice(index, 1);
  }
  dispatchEvent(event) {
    // всплытие: обработчики родителя тоже должны получить событие
    if (!event.target) event.target = this;
    if (event.cancelable === undefined) event.cancelable = true;
    let node = this;
    while (node) {
      event.currentTarget = node;
      for (const handler of (node._listeners || {})[event.type] || []) handler.call(node, event);
      if (node.parentNode) node = node.parentNode;
      else if (node.__document) node = null;
      else node = null;
    }
    return !event.defaultPrevented;
  }
  click() {
    return this.dispatchEvent(makeEvent("click"));
  }
}

function textNode(text) {
  const node = new Node("text");
  node._text = String(text);
  return node;
}

function walk(node, visit) {
  visit(node);
  for (const child of node.children) walk(child, visit);
}

/** Поддержаны простые селекторы: #id, .class, тег и их сочетания через пробел. */
function matches(node, selector) {
  const parts = String(selector).trim().split(/\s+/);
  if (!matchesSimple(node, parts[parts.length - 1])) return false;
  let ancestor = node.parentNode;
  for (let index = parts.length - 2; index >= 0; index -= 1) {
    while (ancestor && !matchesSimple(ancestor, parts[index])) ancestor = ancestor.parentNode;
    if (!ancestor) return false;
    ancestor = ancestor.parentNode;
  }
  return true;
}

function matchesSimple(node, part) {
  const token = part.split(/[\[\]]/)[0];
  if (token.startsWith("#")) return node.id === token.slice(1);
  if (token.startsWith(".")) return node.classList.contains(token.slice(1));
  if (!token) return true;
  return node.tagName === token.toUpperCase();
}

function makeEvent(type, extra) {
  const event = {
    type,
    defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() {},
  };
  return Object.assign(event, extra || {});
}

/** Разбор index.html: теги, атрибуты, вложенность. */
function parseHTML(html) {
  const root = new Node("html");
  const stack = [root];
  const tokens = String(html)
    .replace(/<!--[\s\S]*?-->/g, "")
    .split(/(<[^>]+>)/)
    .filter((piece) => piece !== "");
  for (const token of tokens) {
    if (!token.startsWith("<")) {
      const text = token.replace(/\s+/g, " ");
      if (text.trim()) {
        const parent = stack[stack.length - 1];
        if (parent.children.length === 0) parent._text += text;
        else {
          const node = new Node("text");
          node._text = text;
          parent.appendChild(node);
        }
      }
      continue;
    }
    if (/^<\//.test(token)) {
      if (stack.length > 1) stack.pop();
      continue;
    }
    const selfClosing = /\/>$/.test(token) || /^<(meta|link|img|br|input|hr)\b/i.test(token);
    const name = (/^<\s*([a-zA-Z0-9-]+)/.exec(token) || [])[1];
    if (!name) continue;
    const node = new Node(name);
    for (const match of token.matchAll(/([a-zA-Z0-9_:.-]+)\s*=\s*"([^"]*)"/g)) {
      const [, key, value] = match;
      node.attributes[key] = value;
      if (key === "id") node.id = value;
      if (key === "class") node.className = value;
      if (key === "value") node.value = value;
      if (key === "type") node.type = value;
      if (key === "placeholder") node.placeholder = value;
      if (key === "href") node.href = value;
      if (key === "hidden") node.hidden = true;
      if (key === "checked") node.checked = true;
      if (key === "disabled") node.disabled = true;
      if (key.startsWith("data-")) {
        const dataKey = key.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
        node.dataset[dataKey] = value;
      }
    }
    stack[stack.length - 1].appendChild(node);
    if (!selfClosing) stack.push(node);
  }
  return root;
}

class Document extends Node {
  constructor(html) {
    super("document");
    this.__document = true;
    this.readyState = "complete";
    this.documentElement = parseHTML(html);
    this.documentElement.parentNode = this;
    this.children.push(this.documentElement);
    this.body = this.documentElement.querySelector("body") || new Node("body");
    if (!this.body.parentNode) this.documentElement.appendChild(this.body);
    this.activeElement = null;
    this._listeners = {};
  }
  getElementById(id) {
    return this.documentElement.querySelector(`#${id}`);
  }
  createElement(tag) {
    return new Node(tag);
  }
  createTextNode(text) {
    const node = new Node("text");
    node._text = String(text == null ? "" : text);
    return node;
  }
  appendChild(child) {
    if (child) child.parentNode = this;
    return child;
  }
  addEventListener(type, handler) {
    (this._listeners[type] = this._listeners[type] || []).push(handler);
  }
  dispatchEvent(event) {
    for (const handler of this._listeners[event.type] || []) handler.call(this, event);
    return true;
  }
}

module.exports = { Node, Document, parseHTML, makeEvent };
