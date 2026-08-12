/* Faza 4F — MCQ stanje vezano za IDENTITET zadatka, opstanak aktivnog zadatka
 * kroz sigurnu grešku, i odbacivanje zastarjelih odgovora.
 *
 * PRODUKCIJSKI FIXTURE (doslovno):
 *   „Koji od sljedećih brojeva je djeljiv sa 25?“  ·  322 390 349 375  ·  375 tačno
 *   učenik klikne 390 → crveno, netačno → traži „Daj mi novi zadatak.“
 *   → objavljen ISTI zadatak, ISTE opcije, crveni 390 je OSTAO na ekranu.
 *
 * GRANICA: ovo NIJE pravi pregledač (vidi tests/frontend/browser_stub.js).
 * Nema CSS-a, layouta, stvarne isporuke događaja, MathJaxa ni mreže — pa ovo
 * ne dokazuje da neki sloj vizuelno ne prekriva dugme. Dokazuje se logika
 * rukovalaca i stanje koje oni ostavljaju.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { loadPage, jsonResponse, settle, optionText } = require('./browser_stub.js');

const CHAT = '/api/ai-tutor/chat';
const STREAM = '/api/ai-tutor/chat/stream';
const SAFE_ERROR = 'Nešto je zapelo pri sastavljanju odgovora. Pošalji poruku ponovo za koji trenutak.';

const TASK_A = 'Koji od sljedećih brojeva je djeljiv sa 25?';
const OPTIONS_A = [
  { id: 'a', text: '322' }, { id: 'b', text: '390' },
  { id: 'c', text: '349' }, { id: 'd', text: '375' },
];
const IDENTITY_A = 'sig-task-a';

const TASK_B = 'Koji od sljedećih brojeva je djeljiv sa 10?';
const OPTIONS_B = [
  { id: 'a', text: '41' }, { id: 'b', text: '70' },
  { id: 'c', text: '33' }, { id: 'd', text: '58' },
];
const IDENTITY_B = 'sig-task-b';

function ready(task, options, identity, extra = {}) {
  return {
    status: 'ready',
    answer: 'Evo zadatka.\n\nZadatak: ' + task,
    answer_verdict: null,
    last_tutor_task: task,
    next_state: {
      v: 1, correct_streak: 0, hint_level: 0,
      task: { question: task, options, identity },
    },
    session_mode: 'practice',
    effective_topic: '6-03-004',
    ...extra,
  };
}

/** Sigurna greška u kojoj je server SAČUVAO aktivan zadatak. */
function safeErrorPreserved(task) {
  return { answer: SAFE_ERROR, last_tutor_task: task, task_preserved: true };
}

function practicePage() {
  const page = loadPage();
  page.ui.state.mode = 'practice';
  page.ui.state.grade = 6;
  page.ui.state.topic = '6-03-004';
  page.ui.state.topicOblastId = 'o3';
  page.mark = () => { page._at = page.network.requests.length; };
  page.sent = () => page.network.requests.slice(page._at || 0).filter(r => r.method === 'POST');
  page.mark();
  return page;
}

function respond(page, payload) {
  page.network.responder = async entry => {
    if (entry.method === 'GET') return jsonResponse({ grades: {} });
    if (entry.url === STREAM) return { ok: false, status: 500, headers: { get: () => '' }, json: async () => ({}) };
    return jsonResponse(typeof payload === 'function' ? payload(entry) : payload);
  };
}

function cards(page) {
  return page.ui.optionsBox.querySelectorAll('.mc-option-card');
}

function classesOf(page) {
  return cards(page).map(b => b.dataset.optionId + ':' + (b.classList.value || '-'));
}

/** Objavi zadatak kroz STVARNI put odgovora (applyTutorResponse). */
async function publish(page, payload) {
  respond(page, payload);
  page.doc.getElementById('tutorMessage').value = 'Daj mi zadatak.';
  page.mark();
  await page.ui.sendTutorMsg();
  await settle();
}

async function clickOption(page, optionId) {
  const button = cards(page).find(b => b.dataset.optionId === optionId);
  assert.ok(button, 'opcija ' + optionId + ' ne postoji');
  page.mark();
  await Promise.all(button.click());
  await settle();
  return button;
}

async function sendChip(page, message, chipMeta) {
  page.ui.setChipMeta(chipMeta || null);
  page.doc.getElementById('tutorMessage').value = message;
  page.mark();
  await page.ui.sendTutorMsg();
  await settle();
}

// ---------------------------------------------------------------------------
// 1. DOSLOVAN PRODUKCIJSKI NIZ: fresh → klik 390 → novi zadatak
// ---------------------------------------------------------------------------

test('the exact production sequence resets every per-task visual state', async () => {
  const page = practicePage();
  await publish(page, ready(TASK_A, OPTIONS_A, IDENTITY_A));
  assert.deepStrictEqual(cards(page).map(optionText), ['322', '390', '349', '375']);

  // klik na 390 → netačno
  respond(page, ready(TASK_A, OPTIONS_A, IDENTITY_A, {
    answer: 'Nije tačno — 375 završava sa 75.', answer_verdict: 'incorrect',
  }));
  const wrong = await clickOption(page, 'b');
  assert.strictEqual(page.sent().length, 1, 'klik šalje TAČNO jedan zahtjev');
  assert.strictEqual(page.sent()[0].body.selected_option_id, 'b');
  assert.strictEqual(wrong.classList.contains('incorrect'), true);

  // novi zadatak — STVARNO drugačiji
  await publish(page, ready(TASK_B, OPTIONS_B, IDENTITY_B));

  const after = cards(page);
  assert.deepStrictEqual(after.map(optionText), ['41', '70', '33', '58']);
  assert.strictEqual(after.some(b => b.classList.contains('incorrect')), false,
    'crvena klasa prvog zadatka je preživjela');
  assert.strictEqual(after.some(b => b.classList.contains('correct')), false);
  assert.deepStrictEqual(after.map(b => b.disabled), [false, false, false, false]);
  assert.strictEqual(page.ui.optionsBox.dataset.taskIdentity, IDENTITY_B);
  assert.strictEqual(page.ui.storedLastTask(), TASK_B);
});

test('a republished identical task still resets the visual state', async () => {
  /* Server sada odbija duplikat, ali frontend ne smije ovisiti o tome: ako
     ikad stigne isti tekst s NOVIM identitetom, stanje se mora resetovati. */
  const page = practicePage();
  await publish(page, ready(TASK_A, OPTIONS_A, IDENTITY_A));
  respond(page, ready(TASK_A, OPTIONS_A, IDENTITY_A, { answer_verdict: 'incorrect' }));
  await clickOption(page, 'b');
  assert.strictEqual(cards(page).find(b => b.dataset.optionId === 'b').classList.contains('incorrect'), true);

  await publish(page, ready(TASK_A, OPTIONS_A, 'sig-task-a-second'));
  assert.strictEqual(cards(page).some(b => b.classList.contains('incorrect')), false);
  assert.strictEqual(page.ui.optionsBox.dataset.taskIdentity, 'sig-task-a-second');
});

test('a reveal belonging to another task is never painted on the current one', async () => {
  const page = practicePage();
  await publish(page, ready(TASK_A, OPTIONS_A, IDENTITY_A));
  // Odgovor nosi identitet DRUGOG zadatka uz otkrivenu opciju.
  page.ui.applyTutorResponse(
    ready(TASK_A, OPTIONS_A, 'sig-some-other-task', { revealed_correct_option_id: 'a' }),
    { fromTyped: false, rendered: false });
  await settle();
  assert.strictEqual(cards(page).some(b => b.classList.contains('correct')), false,
    'otkrivanje tuđeg zadatka je obojilo aktivni');
});

// ---------------------------------------------------------------------------
// 2. SIGURNA GREŠKA ČUVA AKTIVAN ZADATAK
// ---------------------------------------------------------------------------

for (const [label, message, chip] of [
  ['hint', 'Ne znam.', { intent: 'hint_request' }],
  ['solution', 'Uradi ga ti.', { intent: 'solution_request' }],
  ['new task', 'Daj mi novi zadatak.', null],
  ['easier', 'Daj mi lakši zadatak.', { difficulty_request: 'easier' }],
  ['harder', 'Daj mi teži zadatak.', { difficulty_request: 'harder' }],
]) {
  test(`a safe error during ${label} keeps the active task and its options`, async () => {
    const page = practicePage();
    await publish(page, ready(TASK_A, OPTIONS_A, IDENTITY_A));
    const before = classesOf(page);

    respond(page, safeErrorPreserved(TASK_A));
    await sendChip(page, message, chip);

    assert.deepStrictEqual(cards(page).map(optionText), ['322', '390', '349', '375'],
      'opcije su nestale iako je server sačuvao zadatak');
    assert.deepStrictEqual(classesOf(page), before);
    assert.strictEqual(page.ui.optionsBox.dataset.taskIdentity, IDENTITY_A);
    assert.deepStrictEqual(cards(page).map(b => b.disabled), [false, false, false, false],
      'dugmad moraju ostati upotrebljiva');
    assert.strictEqual(page.ui.storedLastTask(), TASK_A);
  });
}

test('a safe error keeps a committed wrong selection visible', async () => {
  const page = practicePage();
  await publish(page, ready(TASK_A, OPTIONS_A, IDENTITY_A));
  respond(page, ready(TASK_A, OPTIONS_A, IDENTITY_A, { answer_verdict: 'incorrect' }));
  await clickOption(page, 'b');

  respond(page, safeErrorPreserved(TASK_A));
  await sendChip(page, 'Ne znam.', { intent: 'hint_request' });

  assert.strictEqual(cards(page).find(b => b.dataset.optionId === 'b').classList.contains('incorrect'), true);
});

test('a safe error with no preserved task may show no options', async () => {
  const page = practicePage();
  await publish(page, ready(TASK_A, OPTIONS_A, IDENTITY_A));
  respond(page, { answer: SAFE_ERROR, last_tutor_task: '', task_preserved: false });
  await sendChip(page, 'Daj mi novi zadatak.', null);
  assert.deepStrictEqual(cards(page).map(optionText), []);
});

test('a failed choice request leaves the task and re-enables the options', async () => {
  const page = practicePage();
  await publish(page, ready(TASK_A, OPTIONS_A, IDENTITY_A));
  page.network.responder = async entry => {
    if (entry.method === 'GET') return jsonResponse({ grades: {} });
    throw new Error('network down');
  };
  await clickOption(page, 'b');
  assert.deepStrictEqual(cards(page).map(optionText), ['322', '390', '349', '375']);
  assert.deepStrictEqual(cards(page).map(b => b.disabled), [false, false, false, false]);
});

// ---------------------------------------------------------------------------
// 3. ZASTARJELI ODGOVORI I REDOSLIJED ZAHTJEVA
// ---------------------------------------------------------------------------

test('a slow older response cannot overwrite a newer accepted task', async () => {
  const page = practicePage();
  await publish(page, ready(TASK_A, OPTIONS_A, IDENTITY_A));
  // Noviji zadatak je već prihvaćen…
  await publish(page, ready(TASK_B, OPTIONS_B, IDENTITY_B));
  const generation = page.ui.currentRequestGeneration();

  // …a sada kasni odgovor STARIJEG zahtjeva.
  page.ui.applyTutorResponse(ready(TASK_A, OPTIONS_A, IDENTITY_A), {
    fromTyped: false, rendered: false, requestGeneration: generation - 1,
  });
  await settle();

  assert.strictEqual(page.ui.storedLastTask(), TASK_B);
  assert.deepStrictEqual(cards(page).map(optionText), ['41', '70', '33', '58']);
  assert.strictEqual(page.ui.optionsBox.dataset.taskIdentity, IDENTITY_B);
});

test('a response for another topic cannot commit after switching topics', async () => {
  const page = practicePage();
  await publish(page, ready(TASK_A, OPTIONS_A, IDENTITY_A));
  const generation = page.ui.currentRequestGeneration();
  page.ui.state.topic = '6-03-003';

  page.ui.applyTutorResponse(
    { ...ready(TASK_B, OPTIONS_B, IDENTITY_B), effective_topic: '6-03-004' },
    { fromTyped: false, rendered: false, requestGeneration: generation,
      requestTopic: '6-03-004' });
  await settle();

  /* Praćenje zadatka je vezano za temu, pa `storedLastTask()` nakon promjene
     teme namjerno čita prazno. Mjerodavno je da odgovor STARE teme nije upisao
     ništa vidljivo: kartice i identitet su i dalje od zadatka A. */
  assert.deepStrictEqual(cards(page).map(optionText), ['322', '390', '349', '375']);
  assert.strictEqual(page.ui.optionsBox.dataset.taskIdentity, IDENTITY_A);
});

test('closing the practice view invalidates pending responses', async () => {
  const page = practicePage();
  await publish(page, ready(TASK_A, OPTIONS_A, IDENTITY_A));
  const generation = page.ui.currentRequestGeneration();
  page.ui.invalidatePendingResponses();

  page.ui.applyTutorResponse(ready(TASK_B, OPTIONS_B, IDENTITY_B), {
    fromTyped: false, rendered: false, requestGeneration: generation,
  });
  await settle();

  assert.strictEqual(page.ui.storedLastTask(), TASK_A);
  assert.deepStrictEqual(cards(page).map(optionText), ['322', '390', '349', '375']);
});

test('a timed-out request that finishes later cannot overwrite the retry', async () => {
  const page = practicePage();
  await publish(page, ready(TASK_A, OPTIONS_A, IDENTITY_A));
  const staleGeneration = page.ui.currentRequestGeneration();
  await publish(page, ready(TASK_B, OPTIONS_B, IDENTITY_B));   // uspješan retry

  page.ui.applyTutorResponse(ready(TASK_A, OPTIONS_A, IDENTITY_A), {
    fromTyped: false, rendered: false, requestGeneration: staleGeneration,
  });
  await settle();
  assert.strictEqual(page.ui.optionsBox.dataset.taskIdentity, IDENTITY_B);
});

test('a double new-task click while busy sends one request', async () => {
  const page = practicePage();
  await publish(page, ready(TASK_A, OPTIONS_A, IDENTITY_A));
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  page.network.responder = async entry => {
    if (entry.method === 'GET') return jsonResponse({ grades: {} });
    if (entry.url === STREAM) return { ok: false, status: 500, headers: { get: () => '' }, json: async () => ({}) };
    await gate;
    return jsonResponse(ready(TASK_B, OPTIONS_B, IDENTITY_B));
  };
  page.mark();
  page.doc.getElementById('tutorMessage').value = 'Daj mi novi zadatak.';
  const first = page.ui.sendTutorMsg();
  page.doc.getElementById('tutorMessage').value = 'Daj mi novi zadatak.';
  const second = page.ui.sendTutorMsg();
  release();
  await Promise.all([first, second]);
  await settle();

  assert.strictEqual(page.sent().filter(r => r.url === CHAT).length, 1);
});

test('a curriculum reset (grade/topic change) invalidates in-flight responses', async () => {
  /* Faza 4G, Workstream G scenario 9: promjena razreda/lekcije zove
     invalidatePracticeCurriculumState() (templates/index.html), pa odgovor
     zahtjeva poslanog PRIJE reseta ne smije upisati ništa. */
  const page = practicePage();
  await publish(page, ready(TASK_A, OPTIONS_A, IDENTITY_A));
  const generation = page.ui.currentRequestGeneration();

  page.ui.invalidatePracticeCurriculumState();   // isto što radi gradeSel change

  page.ui.applyTutorResponse(ready(TASK_B, OPTIONS_B, IDENTITY_B), {
    fromTyped: false, rendered: false, requestGeneration: generation,
  });
  await settle();

  assert.deepStrictEqual(cards(page).map(optionText), [],
    'odgovor iz stare sesije je ponovo naslikao opcije poslije reseta');
  assert.strictEqual(page.ui.storedLastTask(), '');
});

test('a malformed response leaves the controls usable and the next turn works', async () => {
  /* Faza 4G, Workstream G scenario 11: server vrati parsabilan JSON bez
     ijednog očekivanog polja. Ne smije biti izuzetka, busy zastavice moraju
     pasti i SLJEDEĆI zahtjev mora normalno proći. */
  const page = practicePage();
  await publish(page, ready(TASK_A, OPTIONS_A, IDENTITY_A));

  respond(page, {});                              // potpuno prazan odgovor
  await sendChip(page, 'Daj mi novi zadatak.', null);

  const flags = page.ui.flags();
  assert.strictEqual(flags.tutorBusy, false, 'tutorBusy je ostao podignut');
  assert.strictEqual(flags.choiceBusy, false, 'choiceBusy je ostao podignut');

  // Oporavak: novi ispravan odgovor se normalno primjenjuje.
  await publish(page, ready(TASK_B, OPTIONS_B, IDENTITY_B));
  assert.deepStrictEqual(cards(page).map(optionText), ['41', '70', '33', '58']);
  assert.strictEqual(page.ui.optionsBox.dataset.taskIdentity, IDENTITY_B);
});

test('stage status shows a safe non-math label during a new-task request', async () => {
  /* Faza 4H, Workstream L: fazne poruke su iz ZATVORENOG skupa i nikad ne
     sadrže matematički niti nerecenziran sadržaj. */
  const page = practicePage();
  await publish(page, ready(TASK_A, OPTIONS_A, IDENTITY_A));
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  page.network.responder = async entry => {
    if (entry.method === 'GET') return jsonResponse({ grades: {} });
    if (entry.url === STREAM) return { ok: false, status: 500, headers: { get: () => '' }, json: async () => ({}) };
    await gate;
    return jsonResponse(ready(TASK_B, OPTIONS_B, IDENTITY_B));
  };
  page.doc.getElementById('tutorMessage').value = 'Daj mi novi zadatak.';
  const pending = page.ui.sendTutorMsg();

  const indicator = page.doc.getElementById('tutorTyping');
  assert.ok(indicator.innerHTML.includes('Pripremam zadatak'),
    'faza pripreme mora biti vidljiva: ' + indicator.innerHTML);
  assert.ok(!indicator.innerHTML.includes('$'), 'bez matematičkog sadržaja');
  assert.ok(!indicator.innerHTML.includes(TASK_B), 'bez nerecenziranog zadatka');

  release();
  await pending;
  await settle();
  assert.strictEqual(indicator.classList.contains('hidden'), true);
});

test('hint chip shows the hint stage label while pending', async () => {
  const page = practicePage();
  await publish(page, ready(TASK_A, OPTIONS_A, IDENTITY_A));
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  page.network.responder = async entry => {
    if (entry.method === 'GET') return jsonResponse({ grades: {} });
    if (entry.url === STREAM) return { ok: false, status: 500, headers: { get: () => '' }, json: async () => ({}) };
    await gate;
    return jsonResponse(safeErrorPreserved(TASK_A));
  };
  page.ui.setChipMeta({ intent: 'hint_request' });
  page.doc.getElementById('tutorMessage').value = 'Ne znam.';
  const pending = page.ui.sendTutorMsg();

  const indicator = page.doc.getElementById('tutorTyping');
  assert.ok(indicator.innerHTML.includes('Sastavljam nagovještaj'),
    indicator.innerHTML);

  release();
  await pending;
  await settle();
});

test('an accepted new task updates text, options and identity together', async () => {
  const page = practicePage();
  await publish(page, ready(TASK_A, OPTIONS_A, IDENTITY_A));
  await publish(page, ready(TASK_B, OPTIONS_B, IDENTITY_B));
  assert.strictEqual(page.ui.storedLastTask(), TASK_B);
  assert.strictEqual(page.ui.optionsBox.dataset.taskIdentity, IDENTITY_B);
  assert.deepStrictEqual(cards(page).map(optionText), ['41', '70', '33', '58']);
  assert.strictEqual(page.ui.optionsBox.dataset.taskText, TASK_B);
});
