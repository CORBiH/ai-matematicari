/* Predložene akcije (chipovi) — DOM-nivo testovi STVARNOG generatora iz
 * templates/index.html (`chipDefs` / `renderChips`).
 *
 * Pokreće se s `node --test tests/frontend/`; pytest ih vozi kroz
 * tests/test_frontend_dom_behaviour.py.
 *
 * ZAŠTO POSTOJI: audit 2026-08-16 je našao dvije žive greške koje statička
 * provjera stringova ne bi uhvatila —
 *   P0: „Sličan zadatak“ u Samo rezultat je slao mode=practice sa
 *       selected_topic='' (Quick nema lekciju) → 400 MISSING_TOPIC, a mod je
 *       do tada već bio promijenjen, pa je učenik ostajao zaglavljen;
 *   P1: „Preporuči mi klip“ je obećavao funkciju koju backend nema
 *       (`intent:'recommend_video'` nije u _UI_ACTION_INTENTS).
 * Ovdje se dokazuje da su oba oblika NEDOSTIŽNA, a ne samo prepravljena.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { loadPage, jsonResponse, settle } = require('./browser_stub.js');

const CHAT = '/api/ai-tutor/chat';
const STREAM = '/api/ai-tutor/chat/stream';

const LESSON = '6-03-004';

/** Stranica s učitanim kanonskim lekcijama (kao poslije GET /topics). */
function page(mode, opts = {}) {
  const p = loadPage();
  p.ui.state.grade = 6;
  p.ui.state.mode = mode;
  p.ui.topicNames[LESSON] = 'Djeljivost sa 2, 5 i 10';
  if (opts.topic !== null) {
    p.ui.state.topic = opts.topic === undefined ? LESSON : opts.topic;
    p.ui.state.topicName = p.ui.topicNames[p.ui.state.topic] || '';
    p.ui.state.topicOblastId = 'o3';
  } else {
    p.ui.state.topic = '';
  }
  p.mark = () => { p._at = p.network.requests.length; };
  p.sent = () => p.network.requests.slice(p._at || 0);
  p.turns = () => p.sent().filter(r => r.method === 'POST');
  p.mark();
  return p;
}

/** Odgovor koji je server STVARNO objavio. */
function ready(mode, extra = {}) {
  return {
    status: 'ready',
    answer: 'Evo odgovora.',
    answer_verdict: null,
    last_tutor_task: '',
    next_state: {},
    session_mode: mode,
    effective_topic: mode === 'quick' ? '' : LESSON,
    ...extra,
  };
}

/** Kapija/nepubliкovan odgovor: server NIKAD ne šalje `status` (matbot/quick.py). */
function gate(answer) {
  return { answer, last_tutor_task: '' };
}

function labels(p, j) {
  p.ui.renderChips(j);
  return p.ui.chipsBox.children.map(b => b.textContent);
}

function respondJsonOnly(p, payload) {
  p.network.responder = async entry => {
    if (entry.method === 'GET') return jsonResponse({ grades: {} });
    if (entry.url === STREAM) {
      return { ok: false, status: 500, headers: { get: () => 'application/json' }, json: async () => ({}), text: async () => '' };
    }
    return jsonResponse(typeof payload === 'function' ? payload(entry) : payload);
  };
}

/** Klikni prečicu po labeli i sačekaj da rukovalac završi. */
async function clickChip(p, j, label) {
  p.ui.renderChips(j);
  const btn = p.ui.chipsBox.children.find(b => b.textContent === label);
  assert.ok(btn, 'nema prečice s labelom ' + label);
  btn.click();
  await settle();
  return btn;
}

// ---------------------------------------------------------------------------
// OBJASNI MI
// ---------------------------------------------------------------------------

test('explain: tačno tri prečice uz normalno objašnjenje', () => {
  const p = page('explain');
  assert.deepStrictEqual(labels(p, ready('explain')), [
    '🐢 Objasni jednostavnije',
    '➕ Još jedan primjer',
    '✏️ Pređi na vježbu',
  ]);
});

test('explain: „Objasni drugačije“ više ne postoji', () => {
  const p = page('explain');
  const all = labels(p, ready('explain')).join(' | ');
  assert.ok(!/drugačije/i.test(all), all);
});

test('explain: bez kanonske lekcije nema prelaska u Vježbu', () => {
  const p = page('explain', { topic: null });
  assert.deepStrictEqual(labels(p, ready('explain')), [
    '🐢 Objasni jednostavnije',
    '➕ Još jedan primjer',
  ]);
});

test('explain: nepoznata (nekanonska) tema takođe skriva prelazak', () => {
  const p = page('explain', { topic: '9-99-999' });
  assert.ok(!labels(p, ready('explain')).includes('✏️ Pređi na vježbu'));
});

test('explain: fallback/ambiguous/invalid odgovor nema nijednu prečicu', () => {
  const p = page('explain');
  for (const status of ['fallback', 'ambiguous', 'invalid']) {
    assert.deepStrictEqual(labels(p, ready('explain', { status })), [], status);
  }
});

test('explain: sigurna greška (odgovor bez `status`) nema prečica', () => {
  const p = page('explain');
  assert.deepStrictEqual(labels(p, gate('Nešto je zapelo pri sastavljanju odgovora.')), []);
});

test('explain: „Pređi na vježbu“ nosi ISTU kanonsku lekciju u Vježbu', async () => {
  const p = page('explain');
  respondJsonOnly(p, ready('practice'));
  await clickChip(p, ready('explain'), '✏️ Pređi na vježbu');
  const turn = p.turns().at(-1);
  assert.strictEqual(p.ui.state.mode, 'practice');
  assert.strictEqual(turn.body.mode, 'practice');
  assert.strictEqual(turn.body.selected_topic, LESSON);
  assert.strictEqual(turn.body.student_message, 'Daj mi jedan zadatak za vježbu.');
});

test('explain: „Objasni jednostavnije“ ostaje u Objasni mi, s istom lekcijom', async () => {
  const p = page('explain');
  respondJsonOnly(p, ready('explain'));
  await clickChip(p, ready('explain'), '🐢 Objasni jednostavnije');
  const turn = p.turns().at(-1);
  assert.strictEqual(p.ui.state.mode, 'explain');
  assert.strictEqual(turn.body.mode, 'explain');
  assert.strictEqual(turn.body.selected_topic, LESSON);
});

// ---------------------------------------------------------------------------
// SAMO REZULTAT (QUICK)
// ---------------------------------------------------------------------------

test('quick: tačno jedna prečica uz riješen zadatak', () => {
  const p = page('quick', { topic: null });
  assert.deepStrictEqual(labels(p, ready('quick')), ['📘 Objasni postupak']);
});

test('quick: „Provjeri odgovor“ i „Sličan zadatak“ su uklonjeni', () => {
  const p = page('quick', { topic: null });
  const all = labels(p, ready('quick')).join(' | ');
  assert.ok(!/Provjeri odgovor/.test(all), all);
  assert.ok(!/Sličan zadatak/.test(all), all);
});

test('quick: slikovne kapije nemaju nijednu prečicu', () => {
  const p = page('quick', { topic: null });
  const gates = [
    'Na slici vidim više zadataka. Napiši koji da riješim.',
    'Ne mogu pouzdano pročitati sve potrebne vrijednosti sa slike. Pošalji jasniju sliku ili prepiši zadatak.',
    'Na slici ne vidim matematički zadatak. Pošalji sliku zadatka ili ga prepiši.',
  ];
  for (const msg of gates) assert.deepStrictEqual(labels(p, gate(msg)), [], msg);
});

test('quick: „Objasni postupak“ ostaje u Samo rezultat', async () => {
  const p = page('quick', { topic: null });
  respondJsonOnly(p, ready('quick'));
  await clickChip(p, ready('quick'), '📘 Objasni postupak');
  const turn = p.turns().at(-1);
  assert.strictEqual(p.ui.state.mode, 'quick');
  assert.strictEqual(turn.body.mode, 'quick');
  assert.strictEqual(turn.body.student_message, 'Objasni mi postupak korak po korak.');
  // Historija ide s porukom — Quick nastavak se rješava iz nje i iz
  // serverskog current_task_context (matbot/quick_context.py).
  assert.ok(Array.isArray(turn.body.conversation_history));
});

// P0 REGRESIJA -------------------------------------------------------------

test('quick: nijedna prečica ne prebacuje u Vježbu (P0: MISSING_TOPIC)', async () => {
  const p = page('quick', { topic: null });
  respondJsonOnly(p, ready('quick'));
  p.ui.renderChips(ready('quick'));
  for (const btn of [...p.ui.chipsBox.children]) {
    btn.click();
    await settle();
    assert.strictEqual(p.ui.state.mode, 'quick', 'prečica „' + btn.textContent + '“ je promijenila mod');
  }
  for (const turn of p.turns()) {
    assert.notStrictEqual(turn.body.mode, 'practice');
    assert.ok(!(turn.body.mode === 'practice' && !turn.body.selected_topic));
  }
});

test('nijedna prečica ne šalje mode=practice bez lekcije', async () => {
  // Prečica koja MIJENJA mod postoji samo u Objasni mi; ako lekcija nestane
  // između rendera i klika, konstrukcijska brana u renderChips je zaustavlja.
  const p = page('explain');
  respondJsonOnly(p, ready('practice'));
  p.ui.renderChips(ready('explain'));
  const btn = p.ui.chipsBox.children.find(b => b.textContent === '✏️ Pređi na vježbu');
  p.ui.state.topic = '';                       // lekcija je u međuvremenu nestala
  btn.click();
  await settle();
  assert.strictEqual(p.ui.state.mode, 'explain');
  assert.strictEqual(p.turns().length, 0);
});

// ---------------------------------------------------------------------------
// VJEŽBAJMO
// ---------------------------------------------------------------------------

test('practice: jezgro prečica dok se čeka odgovor', () => {
  const p = page('practice');
  p.ui.setInteractionPhase('awaiting_practice_answer');
  assert.deepStrictEqual(labels(p, ready('practice')), [
    '🙋 Mala pomoć',
    '👉 Uradi ga ti',
    '➕ Novi zadatak',
  ]);
});

test('practice: težina se nudi tek poslije ocijenjenog odgovora', () => {
  const p = page('practice');
  p.ui.setInteractionPhase('awaiting_practice_answer');
  p.ui.setLastTurnWasGraded(true);
  assert.deepStrictEqual(labels(p, ready('practice')), [
    '🙋 Mala pomoć',
    '👉 Uradi ga ti',
    '➕ Novi zadatak',
    '⬇️ Lakši zadatak',
    '⬆️ Teži zadatak',
  ]);
});

test('practice: poslije riješenog zadatka — ujednačena labela, bez klipa', () => {
  const p = page('practice');
  assert.deepStrictEqual(labels(p, ready('practice')), [
    '➕ Novi zadatak',
    '📘 Objasni mi ovo',
  ]);
});

test('practice: „Preporuči mi klip“ nigdje ne postoji', () => {
  const p = page('practice');
  const seen = [];
  seen.push(...labels(p, ready('practice')));
  p.ui.setInteractionPhase('awaiting_practice_answer');
  seen.push(...labels(p, ready('practice')));
  p.ui.setLastTurnWasGraded(true);
  seen.push(...labels(p, ready('practice')));
  const all = seen.join(' | ');
  assert.ok(!/klip/i.test(all), all);
  assert.ok(!/video/i.test(all), all);
});

// Labela je 2026-09-01 preimenovana u „Mala pomoć"; PORUKA, NAMJERA i faza
// ostaju bajt u bajt iste — ovaj test to i mjeri.
test('practice: hint prečica zadržava TAČAN postojeći payload', async () => {
  const p = page('practice');
  p.ui.setAwaitingPracticeTask('Koji broj je djeljiv sa 6?');
  p.ui.setInteractionPhase('awaiting_practice_answer');
  respondJsonOnly(p, ready('practice'));
  await clickChip(p, ready('practice'), '🙋 Mala pomoć');
  const turn = p.turns().at(-1);
  assert.strictEqual(turn.body.student_message, 'Ne znam.');
  assert.strictEqual(turn.body.intent, 'hint_request');
  assert.strictEqual(turn.body.interaction_phase, 'practice_help');
  assert.strictEqual(turn.body.mode, 'practice');
  assert.strictEqual(turn.body.last_tutor_task, 'Koji broj je djeljiv sa 6?');
});

test('practice: „Uradi ga ti“ zadržava solution_request ugovor', async () => {
  const p = page('practice');
  p.ui.setAwaitingPracticeTask('Koji broj je djeljiv sa 6?');
  p.ui.setInteractionPhase('awaiting_practice_answer');
  respondJsonOnly(p, ready('practice'));
  await clickChip(p, ready('practice'), '👉 Uradi ga ti');
  const turn = p.turns().at(-1);
  assert.strictEqual(turn.body.student_message, 'Uradi ga ti.');
  assert.strictEqual(turn.body.intent, 'solution_request');
  assert.strictEqual(turn.body.interaction_phase, 'practice_help');
});

test('practice: težina i dalje šalje difficulty_request', async () => {
  for (const [label, want] of [['⬇️ Lakši zadatak', 'easier'], ['⬆️ Teži zadatak', 'harder']]) {
    const p = page('practice');
    p.ui.setAwaitingPracticeTask('Zadatak.');
    p.ui.setInteractionPhase('awaiting_practice_answer');
    p.ui.setLastTurnWasGraded(true);
    respondJsonOnly(p, ready('practice'));
    await clickChip(p, ready('practice'), label);
    assert.strictEqual(p.turns().at(-1).body.difficulty_request, want, label);
  }
});

test('practice: „Novi zadatak“ poslije rješenja nosi istu staru poruku', async () => {
  const p = page('practice');
  respondJsonOnly(p, ready('practice'));
  await clickChip(p, ready('practice'), '➕ Novi zadatak');
  assert.strictEqual(p.turns().at(-1).body.student_message, 'Daj mi još jedan zadatak.');
});

// ---------------------------------------------------------------------------
// KONTROLNI
// ---------------------------------------------------------------------------

test('kontrolni: session_mode=exam ne daje nijednu chat prečicu', () => {
  const p = page('exam', { topic: null });
  assert.deepStrictEqual(labels(p, ready('exam')), []);
});

test('kontrolni: exam ne propada u Quick prečice ni kad je state.mode drugi', () => {
  const p = page('quick', { topic: null });
  assert.deepStrictEqual(labels(p, ready('exam')), []);
});

// ---------------------------------------------------------------------------
// MOBILNA GUSTINA
// ---------------------------------------------------------------------------

test('normalna stanja Objasni/Rezultat/Vježba drže najviše 3 prečice', () => {
  const explain = page('explain');
  assert.ok(labels(explain, ready('explain')).length <= 3);
  const quick = page('quick', { topic: null });
  assert.ok(labels(quick, ready('quick')).length <= 3);
  const practice = page('practice');
  assert.ok(labels(practice, ready('practice')).length <= 3);
  practice.ui.setInteractionPhase('awaiting_practice_answer');
  assert.ok(labels(practice, ready('practice')).length <= 3);
});
