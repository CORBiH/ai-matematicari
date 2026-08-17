/* Minimalno browser okruženje za IZVRŠAVANJE stvarne skripte iz templates/index.html.
 *
 * ZAŠTO POSTOJI: projekt nema nijedan JS test alat (nema package.json, jsdom,
 * jest, vitest ni playwright), a Faza 4E je popravila TAČNO ono što živi u
 * pregledaču — klik na MCQ opciju i payload chipa „Uradi ga ti“. Statička
 * provjera stringova u HTML-u ne dokazuje da se dugme ponaša ispravno, a
 * backend FakeLLM testovi ne dodiruju DOM uopšte.
 *
 * ŠTA OVO JESTE: skripta iz index.html se izvršava DOSLOVNO, u `node:vm`
 * kontekstu, nad ovim stubom. Nijedna funkcija se ne prepisuje niti
 * reimplementira — testira se izvorni kod, ne njegova kopija.
 *
 * ŠTA OVO NIJE: pravi pregledač. Nema layouta, CSS-a, stvarne isporuke
 * događaja, MathJaxa ni mrežnog sloja. Selektor engine podržava samo oblike
 * koje sama stranica koristi. Zaključci vrijede za LOGIKU rukovaoca, ne za
 * vizuelni rezultat.
 *
 * JEDINA IZMJENA IZVORA: pred zatvaranje glavnog IIFE-a se ubacuje jedan red
 * koji izlaže interne simbole testu (`globalThis.__ui = …`). IIFE je inače
 * potpuno zatvoren, pa bez toga nijedan njegov simbol nije dohvatljiv. Kod pod
 * testom se time ne mijenja — samo se iz njegovog opsega čita.
 */
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const INDEX_HTML = path.join(__dirname, '..', '..', 'templates', 'index.html');

// --- sitan selektor engine: samo oblici koje stranica stvarno koristi -------
function parseSelector(selector) {
  const parts = [];
  const re = /(\.[A-Za-z0-9_-]+)|(#[A-Za-z0-9_-]+)|(\[[^\]]+\])|([A-Za-z][A-Za-z0-9]*)/g;
  let match;
  while ((match = re.exec(selector)) !== null) {
    if (match[1]) parts.push({ kind: 'class', value: match[1].slice(1) });
    else if (match[2]) parts.push({ kind: 'id', value: match[2].slice(1) });
    else if (match[3]) {
      const body = match[3].slice(1, -1);
      const eq = body.indexOf('=');
      if (eq === -1) parts.push({ kind: 'attr', name: body, value: null });
      else {
        parts.push({
          kind: 'attr',
          name: body.slice(0, eq).trim(),
          value: body.slice(eq + 1).trim().replace(/^["']|["']$/g, ''),
        });
      }
    } else if (match[4]) parts.push({ kind: 'tag', value: match[4].toLowerCase() });
  }
  return parts;
}

function datasetKey(attrName) {
  return attrName.replace(/^data-/, '').replace(/-([a-z])/g, (_, c) => c.toUpperCase());
}

class ClassList {
  constructor() { this._set = new Set(); }
  add(...names) { names.forEach(n => this._set.add(n)); }
  remove(...names) { names.forEach(n => this._set.delete(n)); }
  contains(name) { return this._set.has(name); }
  toggle(name, force) {
    const on = force === undefined ? !this._set.has(name) : !!force;
    if (on) this._set.add(name); else this._set.delete(name);
    return on;
  }
  get value() { return [...this._set].join(' '); }
  toString() { return this.value; }
}

class Element {
  constructor(tag, doc) {
    this.tagName = String(tag || 'div').toUpperCase();
    this._doc = doc;
    this.children = [];
    this.parentNode = null;
    this.classList = new ClassList();
    this.dataset = {};
    this.style = {};
    this.attributes = {};
    this.disabled = false;
    this.hidden = false;
    this.value = '';
    this.type = '';
    this.id = '';
    this.textContent = '';
    this._html = '';
    this._listeners = new Map();
    this.isConnected = true;
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.files = [];
  }
  set className(v) {
    this.classList = new ClassList();
    String(v || '').split(/\s+/).filter(Boolean).forEach(n => this.classList.add(n));
  }
  get className() { return this.classList.value; }
  set innerHTML(v) {
    this._html = String(v == null ? '' : v);
    if (this._html === '') this.children = [];
  }
  get innerHTML() { return this._html; }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === 'id') this.id = String(value);
    if (name.startsWith('data-')) this.dataset[datasetKey(name)] = String(value);
  }
  getAttribute(name) {
    if (name.startsWith('data-')) {
      const key = datasetKey(name);
      if (key in this.dataset) return String(this.dataset[key]);
    }
    return name in this.attributes ? this.attributes[name] : null;
  }
  removeAttribute(name) { delete this.attributes[name]; }
  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    if (this._doc && child.id) this._doc._byId.set(child.id, child);
    return child;
  }
  insertBefore(child) { return this.appendChild(child); }
  remove() {
    if (!this.parentNode) return;
    const index = this.parentNode.children.indexOf(this);
    if (index >= 0) this.parentNode.children.splice(index, 1);
    this.parentNode = null;
  }
  _descendants() {
    const out = [];
    for (const child of this.children) { out.push(child); out.push(...child._descendants()); }
    return out;
  }
  _matches(parts) {
    return parts.every(part => {
      if (part.kind === 'class') return this.classList.contains(part.value);
      if (part.kind === 'id') return this.id === part.value;
      if (part.kind === 'tag') return this.tagName.toLowerCase() === part.value;
      const actual = this.getAttribute(part.name);
      return part.value === null ? actual !== null : actual === part.value;
    });
  }
  querySelectorAll(selector) {
    const parts = parseSelector(selector);
    return this._descendants().filter(node => node._matches(parts));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  addEventListener(type, handler) {
    if (!this._listeners.has(type)) this._listeners.set(type, []);
    this._listeners.get(type).push(handler);
  }
  removeEventListener(type, handler) {
    const list = this._listeners.get(type) || [];
    const index = list.indexOf(handler);
    if (index >= 0) list.splice(index, 1);
  }
  /** Sinhrona isporuka događaja — vraća sve što rukovaoci vrate (npr. Promise). */
  dispatch(type, event = {}) {
    const list = [...(this._listeners.get(type) || [])];
    return list.map(handler => handler.call(this, {
      type, target: this, preventDefault() {}, stopPropagation() {}, ...event,
    }));
  }
  click() { return this.dispatch('click'); }
  focus() {}
  blur() {}
  closest() { return null; }
  scrollIntoView() {}
}

class Doc extends Element {
  constructor() {
    super('#document', null);
    this._doc = this;
    this._byId = new Map();
    this.body = new Element('body', this);
    this.documentElement = new Element('html', this);
    this.head = new Element('head', this);
  }
  createElement(tag) { return new Element(tag, this); }
  createTextNode(text) { const n = new Element('#text', this); n.textContent = text; return n; }
  /** Nepoznat ID se kreira na zahtjev: stranica traži 50 elemenata i svaki
   *  mora postojati da bi se bootstrap izvršio kao u pregledaču. */
  getElementById(id) {
    if (!this._byId.has(id)) {
      const el = new Element('div', this);
      el.id = id;
      this._byId.set(id, el);
      this.body.appendChild(el);
    }
    return this._byId.get(id);
  }
  querySelectorAll(selector) {
    const parts = parseSelector(selector);
    const all = [...this._byId.values(), ...this.body._descendants()];
    return [...new Set(all)].filter(node => node._matches(parts));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

function makeStorage() {
  const map = new Map();
  return {
    getItem: key => (map.has(String(key)) ? map.get(String(key)) : null),
    setItem: (key, value) => { map.set(String(key), String(value)); },
    removeItem: key => { map.delete(String(key)); },
    clear: () => map.clear(),
    key: index => [...map.keys()][index] ?? null,
    get length() { return map.size; },
    _map: map,
  };
}

/**
 * Učitaj stvarnu skriptu iz index.html u stub okruženje.
 * Vraća { ui, doc, win, network } — `ui` su interni simboli stranice.
 */
function loadPage(options = {}) {
  const html = fs.readFileSync(INDEX_HTML, 'utf8');
  const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)]
    .map(match => match[1]);
  const source = blocks.reduce((a, b) => (a.length >= b.length ? a : b), '');
  if (!source.trim()) throw new Error('inline script not found in templates/index.html');

  // Jedini transform: izloži simbole IIFE-a testu (vidi docstring iznad).
  const anchor = source.lastIndexOf('})();');
  if (anchor === -1) throw new Error('main IIFE terminator not found');
  const exported = [
    'sendChoiceAnswer', 'sendTutorMsg', 'buildOptionCards', 'renderOptionsFromResponse',
    'applyTutorResponse', 'state', 'optionsBox', 'setAwaitingPracticeTask',
    'clearAwaitingPracticeTask', 'storedLastTask', 'storedNextState',
    'currentRequestGeneration', 'invalidatePendingResponses', 'clearOptions',
    'invalidatePracticeCurriculumState',
    // „Sutra imam kontrolni“: stanje ekrana, tok testa i predaja.
    'enterExam', 'exitExamToHome', 'startExam', 'submitExam', 'renderExamQuestion',
    'setExamState', 'examState', 'exam', 'examCard', 'examEls',
    // Predložene akcije (chipovi): generator, render i kanonska lekcija za
    // prelazak u Vježbu. `topicNames` je isti objekat koji stranica puni iz
    // /topics, pa ga test popunjava umjesto mreže.
    'chipDefs', 'renderChips', 'chipsBox', 'practiceHandoffTopic', 'topicNames',
  ];
  const probe = `\n;globalThis.__ui = {${exported.join(', ')},`
    + ' setChipMeta: (m) => { pendingChipMeta = m; },'
    + ' setInteractionPhase: (p) => { interactionPhase = p; },'
    + ' setSessionId: (v) => { sessionId = v; },'
    + ' setLastTurnWasGraded: (v) => { lastTurnWasGraded = !!v; },'
    + ' flags: () => ({ tutorBusy, choiceBusy, interactionPhase }),'
    + ' hasActiveTurn: () => activeTurn !== null,'
    + ' };\n';
  const patched = source.slice(0, anchor) + probe + source.slice(anchor);

  const doc = new Doc();
  const network = { requests: [], responder: null };

  /* Roditeljski prozor (u produkciji: Thinkific stranica oko iframea).
   * Postoji da bi test mogao DOKAZATI da „Izađi“ objavljuje tačno jednu
   * `matbot:close` poruku — bez njega bi izlazak bio nedokaziv, a to je jedina
   * radnja koja učenika izvodi iz aplikacije. `options.parent === false`
   * simulira samostalno otvorenu stranicu (niko ne sluša). */
  const parentMessages = [];
  const parentWindow = options.parent === false ? undefined : {
    postMessage(message, targetOrigin) { parentMessages.push({ message, targetOrigin }); },
  };

  const win = {
    parent: parentWindow,
    MathJax: undefined,
    matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
    scrollTo() {},
    addEventListener() {},
    removeEventListener() {},
    // Stranica se namjerno ne podiže izvan Thinkifica; `?t=test` je NJEN
    // vlastiti podržani test-mod (index.html: `isTestMode`), ne zaobilaženje.
    location: { href: 'http://localhost/?t=test', search: '?t=test', pathname: '/' },
    history: { replaceState() {}, pushState() {} },
    navigator: { userAgent: 'node-stub', clipboard: { writeText: async () => {} } },
  };

  const sandbox = {
    window: win,
    document: doc,
    localStorage: makeStorage(),
    sessionStorage: makeStorage(),
    console,
    setTimeout, clearTimeout, setInterval, clearInterval,
    queueMicrotask,
    requestAnimationFrame: cb => setTimeout(() => cb(Date.now()), 0),
    cancelAnimationFrame: id => clearTimeout(id),
    AbortController,
    FormData: class FormData { constructor() { this._d = new Map(); } append(k, v) { this._d.set(k, v); } get(k) { return this._d.get(k); } },
    URL: { createObjectURL: () => 'blob:stub', revokeObjectURL() {} },
    crypto: { randomUUID: () => 'uuid-' + (network.requests.length + 1) },
    Promise, JSON, Math, Date, Object, Array, String, Number, Boolean, RegExp, Error, Set, Map,
    encodeURIComponent, decodeURIComponent, parseInt, parseFloat, isNaN,
    URLSearchParams, TextEncoder, TextDecoder, Blob, atob, btoa,
    fetch: async (url, init = {}) => {
      const body = init.body && typeof init.body === 'string' ? JSON.parse(init.body) : init.body;
      // `signal` se prosljeđuje da testovi mogu i POSMATRATI abort (signal.aborted)
      // i REAGOVATI na njega (odbiti pending promise kao pravi fetch u pregledaču).
      const entry = { url: String(url), method: init.method || 'GET', body, headers: init.headers || {}, signal: init.signal || null };
      network.requests.push(entry);
      if (!network.responder) throw new Error('no network responder configured');
      return network.responder(entry);
    },
  };
  sandbox.globalThis = sandbox;
  sandbox.self = sandbox;
  Object.assign(sandbox, win);          // stranica koristi i gole `location`, `crypto`…

  vm.createContext(sandbox);
  vm.runInContext(patched, sandbox, { filename: 'templates/index.html<script>' });

  // Stranica se podiže na DOMContentLoaded, kao u pregledaču.
  doc.dispatch('DOMContentLoaded');

  const ui = sandbox.__ui;
  if (!ui) throw new Error('page bootstrap did not run');
  if (options.sessionId !== false) ui.setSessionId(options.sessionId || 'test-session');
  return { ui, doc, win, sandbox, network, parentMessages };
}

/** JSON odgovor u obliku koji stvarna ruta vraća. */
function jsonResponse(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: name => (String(name).toLowerCase() === 'content-type' ? 'application/json' : null) },
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  };
}

/** Pusti mikro/makro zadatke da se rukovaoci dovrše. */
async function settle(times = 6) {
  for (let i = 0; i < times; i += 1) await new Promise(resolve => setTimeout(resolve, 0));
}

/** Vidljiv tekst MCQ kartice — ČITA GA KROZ `.mc-option-text` omotač.
 *
 * Kartica je `display:flex`, pa cio tekst opcije mora biti u TAČNO JEDNOM
 * potomku: kad MathJax razbije `$…$` u `mjx-container` elemente, proza između
 * njih inače postane zaseban anoniman flex element i razmaci na njegovim
 * krajevima nestanu („x = 0ilix = 3“). Testovi zato tekst čitaju odavde, a ne
 * s dugmeta — čitanje s dugmeta bi tiho prošlo i kad omotač nestane.
 */
function optionText(card) {
  const wrapper = card.querySelector('.mc-option-text');
  return wrapper === null ? null : wrapper.innerHTML;
}

module.exports = { loadPage, jsonResponse, settle, optionText, Element, Doc };
