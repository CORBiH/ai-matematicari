/* DOM-nivo testovi STVARNIH rukovalaca iz templates/index.html.
 *
 * Pokreće se s `node --test tests/frontend/`; pytest ih vozi kroz
 * tests/test_frontend_dom_behaviour.py. Nijedan paket se ne instalira —
 * koriste se samo `node:test`, `node:assert` i `node:vm`.
 *
 * GRANICA: ovo NIJE pravi pregledač (vidi tests/frontend/browser_stub.js).
 * Dokazuje se logika rukovaoca — koliko zahtjeva ode, šta je u payloadu i
 * kakvo stanje ostane — ne izgled, CSS ni stvarna isporuka događaja.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { loadPage, jsonResponse, settle, optionText } = require('./browser_stub.js');

const TASK = 'Koji od sljedećih brojeva je djeljiv i sa 6 i sa 25?';
const OPTIONS = [
  { id: 'a', text: '90' }, { id: 'b', text: '150' },
  { id: 'c', text: '60' }, { id: 'd', text: '75' },
];

const CHAT = '/api/ai-tutor/chat';
const STREAM = '/api/ai-tutor/chat/stream';
const SAFE_ERROR = 'Nešto je zapelo pri sastavljanju odgovora. Pošalji poruku ponovo za koji trenutak.';

function readyTask(extra = {}) {
  return {
    status: 'ready',
    answer: 'Evo zadatka.\n\nZadatak: ' + TASK,
    answer_verdict: null,
    last_tutor_task: TASK,
    next_state: { v: 1, correct_streak: 0, hint_level: 0, task: { question: TASK, options: OPTIONS } },
    session_mode: 'practice',
    effective_topic: '6-03-004',
    ...extra,
  };
}

/** Stranica u Vježbi s objavljenim zadatkom i izgrađenim karticama. */
function practicePage() {
  const page = loadPage();
  page.ui.state.mode = 'practice';
  page.ui.state.grade = 6;
  page.ui.state.topic = '6-03-004';
  page.ui.state.topicOblastId = 'o3';
  // Kartice pravi STVARNI kod stranice, uključujući registraciju click rukovaoca.
  page.ui.buildOptionCards(OPTIONS, TASK);
  page.ui.setAwaitingPracticeTask(TASK);
  page.ui.setInteractionPhase('awaiting_practice_answer');
  // Bootstrap sam po sebi radi GET /topics; mjeri se SAMO ono poslije njega.
  page.mark = () => { page._at = page.network.requests.length; };
  page.sent = () => page.network.requests.slice(page._at || 0);
  page.sentTurns = () => page.sent().filter(r => r.method === 'POST');
  page.mark();
  return page;
}

function cards(page) {
  return page.ui.optionsBox.querySelectorAll('.mc-option-card');
}

/** SSE nije dostupan (kao iza proxyja koji blokira stream) → stvarni JSON fallback. */
function respondJsonOnly(page, payload) {
  page.network.responder = async entry => {
    if (entry.method === 'GET') return jsonResponse({ grades: {} });
    if (entry.url === STREAM) return { ok: false, status: 500, headers: { get: () => 'application/json' }, json: async () => ({}), text: async () => '' };
    return jsonResponse(typeof payload === 'function' ? payload(entry) : payload);
  };
}

// ---------------------------------------------------------------------------
// 1. KLIK NA MCQ OPCIJU
// ---------------------------------------------------------------------------

test('option button fires exactly one request', async () => {
  const page = practicePage();
  respondJsonOnly(page, readyTask({ answer: 'Tačno!', answer_verdict: 'correct' }));

  const button = cards(page).find(b => b.dataset.optionId === 'b');
  await Promise.all(button.click());
  await settle();

  assert.strictEqual(page.sentTurns().length, 1, 'klik smije poslati TAČNO jedan zahtjev');
  assert.strictEqual(page.sentTurns()[0].url, CHAT, 'klik ne koristi SSE put');
});

test('option click sends the selected option id and the choice contract', async () => {
  const page = practicePage();
  respondJsonOnly(page, readyTask({ answer: 'Tačno!', answer_verdict: 'correct' }));

  const button = cards(page).find(b => b.dataset.optionId === 'c');
  await Promise.all(button.click());
  await settle();

  const body = page.sentTurns()[0].body;
  assert.strictEqual(body.interaction_type, 'choice_answer');
  assert.strictEqual(body.selected_option_id, 'c');
  assert.strictEqual(body.mode, 'practice');
  assert.strictEqual(body.selected_topic, '6-03-004');
  assert.ok(body.client_turn_id, 'klik mora nositi client_turn_id (idempotencija)');
});

test('a double click while the request is in flight sends nothing extra', async () => {
  const page = practicePage();
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  page.network.responder = async entry => {
    if (entry.method === 'GET') return jsonResponse({ grades: {} });
    if (entry.url === STREAM) return { ok: false, status: 500, headers: { get: () => '' }, json: async () => ({}) };
    await gate;
    return jsonResponse(readyTask({ answer: 'Tačno!', answer_verdict: 'correct' }));
  };

  const button = cards(page).find(b => b.dataset.optionId === 'b');
  const first = button.click();
  const second = cards(page).find(b => b.dataset.optionId === 'a').click();
  release();
  await Promise.all([...first, ...second]);
  await settle();

  assert.strictEqual(page.sentTurns().length, 1, 'drugi klik dok traje zahtjev mora biti odbijen');
});

test('loading state is cleared after a successful click', async () => {
  const page = practicePage();
  respondJsonOnly(page, readyTask({ answer: 'Tačno!', answer_verdict: 'correct' }));

  const button = cards(page).find(b => b.dataset.optionId === 'b');
  await Promise.all(button.click());
  await settle();

  assert.strictEqual(button.classList.contains('loading'), false);
  const flags = page.ui.flags();
  assert.strictEqual(flags.tutorBusy, false);
  assert.strictEqual(flags.choiceBusy, false);
});

test('loading state is cleared when the request throws', async () => {
  const page = practicePage();
  page.network.responder = async entry => {
    if (entry.method === 'GET') return jsonResponse({ grades: {} });
    throw new Error('network down');
  };

  const button = cards(page).find(b => b.dataset.optionId === 'b');
  await Promise.all(button.click());
  await settle();

  assert.strictEqual(button.classList.contains('loading'), false, 'loading klasa mora nestati i na grešci');
  assert.strictEqual(page.ui.flags().tutorBusy, false);
  assert.strictEqual(page.ui.flags().choiceBusy, false);
});

test('a rejected request never leaves the options permanently disabled', async () => {
  const page = practicePage();
  // Deterministički serverski blok: sigurna poruka BEZ `status` i `next_state`.
  respondJsonOnly(page, { answer: SAFE_ERROR, last_tutor_task: TASK });

  const button = cards(page).find(b => b.dataset.optionId === 'b');
  await Promise.all(button.click());
  await settle();

  const disabled = cards(page).filter(b => b.disabled).map(b => b.dataset.optionId);
  assert.deepStrictEqual(disabled, [], 'nijedna opcija ne smije ostati onemogućena nakon odbijenog zahtjeva');
});

test('options stay clickable after a thrown request', async () => {
  const page = practicePage();
  page.network.responder = async entry => {
    if (entry.method === 'GET') return jsonResponse({ grades: {} });
    throw new Error('network down');
  };
  await Promise.all(cards(page).find(b => b.dataset.optionId === 'b').click());
  await settle();

  assert.deepStrictEqual(cards(page).filter(b => b.disabled).map(b => b.dataset.optionId), []);

  respondJsonOnly(page, readyTask({ answer: 'Tačno!', answer_verdict: 'correct' }));
  page.mark();
  await Promise.all(cards(page).find(b => b.dataset.optionId === 'b').click());
  await settle();
  assert.strictEqual(page.sentTurns().length, 1, 'ponovni klik nakon greške mora proći');
});

test('first wrong click re-enables the other options and reveals nothing', async () => {
  const page = practicePage();
  respondJsonOnly(page, readyTask({ answer: 'Nije tačno.', answer_verdict: 'incorrect' }));

  const clicked = cards(page).find(b => b.dataset.optionId === 'a');
  await Promise.all(clicked.click());
  await settle();

  assert.strictEqual(clicked.disabled, true, 'kliknuta opcija ostaje onemogućena');
  const others = cards(page).filter(b => b !== clicked);
  assert.deepStrictEqual(others.map(b => b.disabled), [false, false, false]);
  assert.strictEqual(others.some(b => b.classList.contains('correct')), false);
});

// ---------------------------------------------------------------------------
// 2. CHIP „URADI GA TI“ — P0-B
// ---------------------------------------------------------------------------

/** Pošalji poruku kroz stvarni `sendTutorMsg` s meta podacima chipa. */
async function sendChip(page, message, chipMeta) {
  page.ui.setChipMeta(chipMeta);
  page.doc.getElementById('tutorMessage').value = message;
  page.mark();
  await page.ui.sendTutorMsg();
  await settle();
  return page.sentTurns().map(r => r.body);
}

test('"Uradi ga ti" sends intent=solution_request', async () => {
  const page = practicePage();
  respondJsonOnly(page, readyTask({ answer: 'Evo postupka.', revealed_correct_option_id: 'b' }));

  const [body] = await sendChip(page, 'Uradi ga ti.', { intent: 'solution_request' });
  assert.strictEqual(body.intent, 'solution_request');
});

test('"Uradi ga ti" never sends interaction_phase=answering_practice_task', async () => {
  const page = practicePage();
  respondJsonOnly(page, readyTask({ answer: 'Evo postupka.', revealed_correct_option_id: 'b' }));

  const [body] = await sendChip(page, 'Uradi ga ti.', { intent: 'solution_request' });
  assert.notStrictEqual(body.interaction_phase, 'answering_practice_task',
    'rješenje se nikad ne smije predstaviti kao pokušaj odgovora');
  assert.strictEqual(body.interaction_phase, 'practice_help');
  assert.strictEqual(body.mode, 'practice');
  assert.strictEqual(body.last_tutor_task, TASK, 'server mora dobiti aktivan zadatak');
});

test('every transport attempt of the solution click carries the same contract', async () => {
  const page = practicePage();
  // SSE ovdje ne pukne prije slanja: oba pokušaja moraju nositi isti ugovor.
  page.network.responder = async entry => {
    if (entry.method === 'GET') return jsonResponse({ grades: {} });
    if (entry.url === STREAM) return { ok: false, status: 500, headers: { get: () => '' }, json: async () => ({}) };
    return jsonResponse(readyTask({ answer: 'Evo postupka.', revealed_correct_option_id: 'b' }));
  };
  page.ui.setChipMeta({ intent: 'solution_request' });
  page.doc.getElementById('tutorMessage').value = 'Uradi ga ti.';
  await page.ui.sendTutorMsg();
  await settle();

  assert.ok(page.sentTurns().length >= 1);
  for (const request of page.sentTurns()) {
    assert.strictEqual(request.body.intent, 'solution_request', request.url);
    assert.notStrictEqual(request.body.interaction_phase, 'answering_practice_task', request.url);
  }
});

test('hint chip still targets the active task', async () => {
  const page = practicePage();
  respondJsonOnly(page, readyTask({ answer: 'Pogledaj posljednje dvije cifre.' }));

  const [body] = await sendChip(page, 'Ne znam.', { intent: 'hint_request' });
  assert.strictEqual(body.intent, 'hint_request');
  assert.strictEqual(body.interaction_phase, 'practice_help');
  assert.strictEqual(body.last_tutor_task, TASK);
  assert.strictEqual(body.mode, 'practice');
});

test('a typed short answer is still an answer attempt, not a help request', async () => {
  const page = practicePage();
  respondJsonOnly(page, readyTask({ answer: 'Hajde da provjerimo.' }));

  const [body] = await sendChip(page, '150', null);
  assert.strictEqual(body.interaction_phase, 'answering_practice_task');
  assert.strictEqual(body.intent, undefined);
});

// ---------------------------------------------------------------------------
// 3. OPORAVAK NAKON SIGURNE PORUKE
// ---------------------------------------------------------------------------

test('a safe error hides the option cards but never leaves a stale set behind', async () => {
  /* ZATEČENO PONAŠANJE, zabilježeno namjerno: `renderOptionsFromResponse` za
     svaki odgovor bez `status:'ready'` poziva `clearOptions()`. Kartice tada
     nestanu, iako server aktivan zadatak ZADRŽAVA i vraća ga u
     `last_tutor_task`. Posljedica je blaža varijanta iste stvari koju ovaj
     test i traži: nijedna zastarjela opcija ne ostaje na ekranu. Nije uvedeno
     Fazom 4E i ovdje se ne mijenja — bilježi se da bi promjena bila vidljiva. */
  const page = practicePage();
  assert.strictEqual(cards(page).length, 4);
  respondJsonOnly(page, { answer: SAFE_ERROR, last_tutor_task: TASK });

  await sendChip(page, 'Daj mi novi zadatak.', null);

  assert.deepStrictEqual(cards(page).map(optionText), [],
    'nijedna zastarjela kartica ne smije preživjeti sigurnu poruku');
  assert.strictEqual(page.ui.optionsBox.dataset.taskText, '',
    'potpis zadatka se briše, pa sljedeći ready odgovor gradi svjež set');
});

test('retrying after a safe error does not keep stale options once a new task arrives', async () => {
  const page = practicePage();
  respondJsonOnly(page, { answer: SAFE_ERROR, last_tutor_task: TASK });
  await sendChip(page, 'Daj mi novi zadatak.', null);

  const NEW_TASK = 'Koji od sljedećih brojeva je djeljiv sa 9?';
  const NEW_OPTIONS = [
    { id: 'a', text: '81' }, { id: 'b', text: '17' },
    { id: 'c', text: '22' }, { id: 'd', text: '35' },
  ];
  respondJsonOnly(page, {
    status: 'ready', answer: 'Evo zadatka.\n\nZadatak: ' + NEW_TASK,
    answer_verdict: null, last_tutor_task: NEW_TASK,
    next_state: { v: 1, correct_streak: 0, hint_level: 0, task: { question: NEW_TASK, options: NEW_OPTIONS } },
    session_mode: 'practice', effective_topic: '6-03-004',
  });
  await sendChip(page, 'Daj mi novi zadatak.', null);

  assert.deepStrictEqual(cards(page).map(optionText), ['81', '17', '22', '35'],
    'stare opcije moraju biti u potpunosti zamijenjene');
  assert.deepStrictEqual(cards(page).map(b => b.disabled), [false, false, false, false]);
  assert.strictEqual(page.ui.storedLastTask(), NEW_TASK);
});

test('a solution turn does not rebuild the option cards', async () => {
  const page = practicePage();
  const before = cards(page).map(b => b.dataset.optionId + ':' + optionText(b));
  respondJsonOnly(page, readyTask({ answer: 'Evo postupka.', revealed_correct_option_id: 'b' }));

  await sendChip(page, 'Uradi ga ti.', { intent: 'solution_request' });

  const after = cards(page);
  assert.deepStrictEqual(after.map(b => b.dataset.optionId + ':' + optionText(b)), before,
    'rješenje rješava POSTOJEĆI zadatak — kartice se ne prave iznova');
  assert.strictEqual(after.find(b => b.dataset.optionId === 'b').classList.contains('correct'), true);
  assert.deepStrictEqual(after.map(b => b.disabled), [true, true, true, true],
    'nakon otkrivenog rješenja zadatak je zatvoren');
});
