/* DOM-nivo regresija za NAPUŠTENI TURN u Vježbi (živi nalaz, avgust 2026):
 * učenik ode "Nazad" dok se prvi zadatak još generiše, izabere drugu lekciju —
 * i novi razgovor ostane trajno prazan. Uzrok: tutorBusy je čistio SAMO finally
 * turna u letu, a njegov AbortController je bio lokalan, pa navigacija zahtjev
 * nije mogla ni prekinuti ni osloboditi busy stanje; auto-start nove lekcije je
 * tiho progutan na `if (tutorBusy) return;`.
 *
 * Pokreće se s `node --test tests/frontend/`; pytest ih vozi kroz
 * tests/test_frontend_dom_behaviour.py. Samo `node:test`/`node:assert`/`node:vm`.
 *
 * GRANICA: nije pravi pregledač (vidi tests/frontend/browser_stub.js) — dokazuje
 * se logika rukovalaca (koji zahtjevi odu, koje stanje ostane), ne izgled.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { loadPage, jsonResponse, settle } = require('./browser_stub.js');

const STREAM = '/api/ai-tutor/chat/stream';
const AUTO_MSG = 'Daj mi jedan zadatak za vježbu iz ove teme.';

/* Odgovor kakav ruta stvarno vraća za uspješan practice turn; effective_topic
 * prati zahtjev da stale-guard u applyTutorResponse ne odbaci odgovor. */
function readyTask(topic) {
  return {
    status: 'ready',
    answer: 'Zadatak: Izračunaj 2 + 3.',
    answer_verdict: null,
    last_tutor_task: 'Izračunaj 2 + 3.',
    next_state: { v: 1, correct_streak: 0, hint_level: 0 },
    session_mode: 'practice',
    effective_topic: topic || '',
  };
}

/* Pending POST koji se, kao pravi fetch u pregledaču, ODBIJE s AbortError kad
 * navigacija prekine njegov signal. Test zadržava ručke za resolve/reject. */
function abortablePending(entry, pendingLog) {
  return new Promise((resolve, reject) => {
    pendingLog.push({ entry, resolve, reject });
    if (entry.signal) {
      entry.signal.addEventListener('abort', () => {
        const err = new Error('The user aborted a request.');
        err.name = 'AbortError';
        reject(err);
      });
    }
  });
}

/* Izbor lekcije kroz STVARNI onboarding rukovalac (continueBtn), ne direktnim
 * pozivom sendTutorMsg — bug živi upravo u ovom putu. */
function selectLesson(page, topicId) {
  const doc = page.doc;
  doc.getElementById('homeOblastSelect').value = 'Brojevi';
  const topicSelect = doc.getElementById('homeTopicSelect');
  topicSelect.value = topicId;
  topicSelect.options = [{ dataset: { oblastId: 'OB1' } }];
  topicSelect.selectedIndex = 0;
  doc.getElementById('homeContinue').click();
}

function practiceHome() {
  const page = loadPage();
  page.ui.state.mode = 'practice';
  page.ui.state.grade = '6';
  page.posts = () => page.network.requests.filter(r => r.method === 'POST');
  page.bubbles = () => page.doc.getElementById('tutorChat')
    .querySelectorAll('.tbubble').map(b => b.innerHTML);
  return page;
}

/* Responder sa dvije faze: 'pending' drži POST otvorenim (uz abort ručke),
 * 'ready' završava turn — stream vrati JSON (ne-SSE) pa stvarni kod padne
 * nazad na /chat, koji dobije potpun ready odgovor. */
function phasedResponder(page, pendingLog, opts = {}) {
  const state = { phase: 'pending' };
  page.network.responder = entry => {
    if (entry.method === 'GET') return jsonResponse({ grouped: {}, oblast_order: [] });
    if (state.phase === 'ready') {
      if (entry.url === STREAM) return jsonResponse({});
      return jsonResponse(readyTask(entry.body.selected_topic));
    }
    if (opts.ignoreAbort) {
      // turn koji NE reaguje na abort — za simulaciju zakašnjelog finally-ja
      return new Promise((resolve, reject) => { pendingLog.push({ entry, resolve, reject }); });
    }
    return abortablePending(entry, pendingLog);
  };
  return state;
}

function assertNoErrorBubble(page) {
  for (const html of page.bubbles()) {
    assert.ok(html.indexOf('⏳') === -1, 'namjeran prekid ne smije ispisati timeout poruku: ' + html);
    assert.ok(html.indexOf('Greška pri slanju') === -1, 'namjeran prekid ne smije ispisati grešku: ' + html);
  }
}

// ---------------------------------------------------------------------------
// 1. NORMALAN AUTO-START (osnovni ugovor izbora lekcije)
// ---------------------------------------------------------------------------

test('selecting a lesson auto-starts the first practice turn', async () => {
  const page = practiceHome();
  const pendingLog = [];
  const phase = phasedResponder(page, pendingLog);
  phase.phase = 'ready';

  selectLesson(page, 'T1');
  await settle(20);

  const first = page.posts()[0];
  assert.ok(first, 'izbor lekcije mora automatski poslati prvi zahtjev');
  assert.strictEqual(first.body.mode, 'practice');
  assert.strictEqual(first.body.student_message, AUTO_MSG);
  assert.strictEqual(first.body.selected_topic, 'T1');
  assert.strictEqual(first.body.selected_oblast, 'OB1');
  assert.strictEqual(first.body.entry_source, 'manual_topic_choice');
  assert.strictEqual(page.ui.flags().tutorBusy, false, 'busy se mora osloboditi po završetku turna');
  assert.strictEqual(page.ui.hasActiveTurn(), false);
  assert.ok(page.doc.getElementById('tutorEmptyState').classList.contains('hidden'),
    'empty-state mora nestati čim krene prvi turn');
  assert.ok(page.bubbles().some(h => h.indexOf('Izračunaj') !== -1), 'zadatak mora biti iscrtan');
});

// ---------------------------------------------------------------------------
// 2. PRIJAVLJENI BUG: Nazad tokom generisanja → druga lekcija mora startati
// ---------------------------------------------------------------------------

test('Nazad during a pending turn cancels it and the next lesson auto-starts', async () => {
  const page = practiceHome();
  const pendingLog = [];
  const phase = phasedResponder(page, pendingLog);

  selectLesson(page, 'T1');
  await settle(5);
  assert.strictEqual(page.posts().length, 1, 'turn A mora biti u letu');
  assert.strictEqual(page.ui.flags().tutorBusy, true);

  // učenik ode "Nazad" dok A još traje (bez ocijenjenih zadataka → direktan izlaz)
  page.doc.getElementById('tutorBackBtn').click();
  await settle(5);

  assert.strictEqual(pendingLog[0].entry.signal.aborted, true, 'napušteni turn A mora biti prekinut (abort)');
  assert.strictEqual(page.ui.flags().tutorBusy, false, 'busy ne smije ostati podignut poslije izlaska');
  assert.strictEqual(page.ui.hasActiveTurn(), false);

  // nova lekcija mora normalno auto-startati
  phase.phase = 'ready';
  selectLesson(page, 'T2');
  await settle(20);

  const turnB = page.posts().filter(r => r.body.selected_topic === 'T2');
  assert.ok(turnB.length >= 1, 'lekcija B mora automatski poslati svoj prvi zahtjev');
  assert.strictEqual(turnB[0].body.student_message, AUTO_MSG);
  assert.ok(page.doc.getElementById('tutorEmptyState').classList.contains('hidden'),
    'ekran ne smije ostati trajno prazan');
  assert.ok(page.bubbles().some(h => h.indexOf('Izračunaj') !== -1), 'zadatak lekcije B mora biti iscrtan');
  assertNoErrorBubble(page);
  assert.strictEqual(page.ui.flags().tutorBusy, false);
});

// ---------------------------------------------------------------------------
// 3. TRKA: zakašnjeli finally starog turna ne smije oboriti stanje novog
// ---------------------------------------------------------------------------

test('a stale finally from the cancelled turn cannot clear the new turn state', async () => {
  const page = practiceHome();
  const pendingLog = [];
  // A ignoriše abort: njegov promise se rješava tek RUČNO, poslije starta B
  const phase = phasedResponder(page, pendingLog, { ignoreAbort: true });

  selectLesson(page, 'T1');
  await settle(5);
  const turnA = pendingLog[0];
  assert.ok(turnA, 'turn A mora biti u letu');

  page.doc.getElementById('tutorBackBtn').click();
  await settle(5);
  assert.strictEqual(page.ui.flags().tutorBusy, false);

  selectLesson(page, 'T2');
  await settle(5);
  const turnB = pendingLog[1];
  assert.ok(turnB && turnB.entry.body.selected_topic === 'T2', 'turn B mora krenuti');
  assert.strictEqual(page.ui.flags().tutorBusy, true, 'B drži busy dok traje');
  assert.strictEqual(page.ui.hasActiveTurn(), true);

  // TEK SAD stigne zakašnjelo odbijanje starog turna A (abort iz navigacije)
  const abortErr = new Error('The user aborted a request.');
  abortErr.name = 'AbortError';
  turnA.reject(abortErr);
  await settle(10);

  assert.strictEqual(page.ui.flags().tutorBusy, true,
    'zakašnjeli finally turna A ne smije osloboditi busy stanje turna B');
  assert.strictEqual(page.ui.hasActiveTurn(), true,
    'zakašnjeli finally turna A ne smije ukloniti aktivni token turna B');
  assert.strictEqual(page.doc.getElementById('tutorTyping').classList.contains('hidden'), false,
    'typing indikator turna B mora ostati vidljiv');
  assertNoErrorBubble(page);

  // B se zatim normalno završava i sam oslobađa svoje stanje
  phase.phase = 'ready';
  turnB.resolve(jsonResponse({}));      // stream → JSON → stvarni fallback na /chat
  await settle(20);
  assert.ok(page.bubbles().some(h => h.indexOf('Izračunaj') !== -1), 'zadatak turna B mora biti iscrtan');
  assert.strictEqual(page.ui.flags().tutorBusy, false);
  assert.strictEqual(page.ui.hasActiveTurn(), false);
});

// ---------------------------------------------------------------------------
// 4. DUPLI SUBMIT U ISTOM RAZGOVORU OSTAJE BLOKIRAN
// ---------------------------------------------------------------------------

test('a duplicate submit while the same conversation turn is active is still blocked', async () => {
  const page = practiceHome();
  const pendingLog = [];
  const phase = phasedResponder(page, pendingLog);

  selectLesson(page, 'T1');
  await settle(5);
  assert.strictEqual(page.posts().length, 1);

  // pokušaj drugog slanja DOK turn traje — mora biti odbijen bez zahtjeva
  page.doc.getElementById('tutorMessage').value = '42';
  page.doc.getElementById('tutorSend').click();
  await settle(5);
  assert.strictEqual(page.posts().length, 1, 'dupli submit ne smije poslati novi zahtjev');
  assert.strictEqual(page.ui.flags().tutorBusy, true);

  // turn se završava: stream → JSON → fallback /chat (ISTI client_turn_id)
  phase.phase = 'ready';
  pendingLog[0].resolve(jsonResponse({}));
  await settle(20);
  const posts = page.posts();
  assert.strictEqual(posts.length, 2, 'poslije završetka postoje samo stream+fallback istog poteza');
  assert.strictEqual(posts[0].body.client_turn_id, posts[1].body.client_turn_id,
    'fallback mora nositi ISTI client_turn_id (jedan logički potez)');
  assert.strictEqual(page.ui.flags().tutorBusy, false);
});

// ---------------------------------------------------------------------------
// 5. OBRIŠI (startFreshTutorConversation) — drugi stvarni put napuštanja
// ---------------------------------------------------------------------------

test('Obriši during a pending turn cancels it and a fresh lesson auto-starts', async () => {
  const page = practiceHome();
  const pendingLog = [];
  const phase = phasedResponder(page, pendingLog);

  selectLesson(page, 'T1');
  await settle(5);
  assert.strictEqual(page.ui.flags().tutorBusy, true);

  // modal element mora postojati prije closeModal poziva u doClear rukovaocu
  page.doc.getElementById('confirm-clear');
  page.doc.getElementById('doClear').click();
  await settle(5);

  assert.strictEqual(pendingLog[0].entry.signal.aborted, true, 'Obriši mora prekinuti turn u letu');
  assert.strictEqual(page.ui.flags().tutorBusy, false);
  assert.strictEqual(page.ui.hasActiveTurn(), false);

  phase.phase = 'ready';
  selectLesson(page, 'T2');
  await settle(20);
  const turnB = page.posts().filter(r => r.body.selected_topic === 'T2');
  assert.ok(turnB.length >= 1, 'nova lekcija poslije Obriši mora auto-startati');
  assert.ok(page.bubbles().some(h => h.indexOf('Izračunaj') !== -1));
  assertNoErrorBubble(page);
  assert.strictEqual(page.ui.flags().tutorBusy, false);
});
