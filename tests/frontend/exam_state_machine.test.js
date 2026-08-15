/* „SUTRA IMAM KONTROLNI“ — JEDNO VIDLJIVO STANJE ISTOVREMENO.
 *
 * ŽIVI PRODUKCIJSKI NALAZ (screenshot, 2026-08-16): poslije ZAVRŠENOG testa
 * učenik je na istom ekranu istovremeno gledao:
 *     „Pripremam test iz oblasti…“      (staro GENERATING stanje)
 *     „Nismo uspjeli pripremiti test.“  (staro GENERATION_ERROR stanje)
 *     rezultat i preporuku               (novo RESULT stanje)
 *
 * UZROK NIJE BIO U LOGICI PREBACIVANJA: `examShow()` je uredno postavljao
 * atribut `hidden` na sve sekcije osim jedne. Uzrok je bio ČISTO CSS: sekcije
 * su imale klase `.exam-body{display:flex}` i `.exam-loading{display:grid}`, a
 * autorski stylesheet uvijek pobjeđuje UA pravilo `[hidden]{display:none}`
 * (ista specifičnost, autor ima veći prioritet). Atribut `hidden` je time bio
 * bez ijednog efekta i sva četiri stanja su se iscrtavala jedno ispod drugog.
 *
 * POPRAVKA JE STRUKTURNA, NE DOGOVORNA: vidljivost više ne ovisi ni o jednom
 * atributu po sekciji, nego o JEDNOJ vrijednosti `data-state` na kartici, uz
 * CSS koji prikazuje samo sekciju koja joj odgovara. Dva stanja se ne mogu
 * preklopiti jer jedan atribut ne može imati dvije vrijednosti.
 *
 * GRANICA (izričito, kao i ostali testovi u ovom folderu): `browser_stub.js`
 * nije pravi pregledač — nema layouta, CSS kaskade, MathJaxa ni mreže. Ovdje
 * se dokazuje (a) da stvarni rukovaoci iz index.html drže tačno jedno stanje i
 * (b) da CSS ugovor koji to čini vizuelno istinitim postoji u samom dokumentu.
 * Piksele dokazuje živa produkcijska provjera, ne ovaj test.
 *
 * FALSIFIKACIJA: vraćanje `examShow()`-a koji skriva sekcije atributom
 * `hidden` (ili uklanjanje `[data-exam-state]{display:none}` pravila) obara
 * `test 'CSS ugovor…'` i `test 'rezultat ne može koegzistirati…'`.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { loadPage, jsonResponse, settle } = require('./browser_stub.js');

const INDEX_HTML = fs.readFileSync(
  path.join(__dirname, '..', '..', 'templates', 'index.html'), 'utf8');

const QUESTIONS = [1, 2, 3, 4, 5].map(n => ({
  id: 'q' + n,
  ordinal: n,
  text: 'Pitanje broj ' + n + ': koliko je $' + n + ' + 1$?',
  options: [
    { id: 'a', text: '$' + (n + 1) + '$' },
    { id: 'b', text: '$' + (n + 2) + '$' },
    { id: 'c', text: '$' + (n + 3) + '$' },
    { id: 'd', text: '$' + (n + 4) + '$' },
  ],
}));

const READY = {
  status: 'ready', exam_id: 'EXAM-1', difficulty: 'standard',
  oblast_name: 'Razlomci', question_count: 5, questions: QUESTIONS,
};

const GRADED = {
  status: 'graded', exam_id: 'EXAM-1', score: 4, total: 5, percentage: 80,
  questions: QUESTIONS.map((q, i) => ({
    id: q.id, ordinal: q.ordinal, correct: i !== 2,
    selected_option_id: 'a', correct_option_id: 'b',
    correct_text: '$tačno' + q.ordinal + '$', lesson_title: 'Lekcija ' + q.ordinal,
  })),
  recommendation: { lessons: ['Lekcija 3'], message: 'Vrlo dobro.' },
};

/* Stranica pod testom + mreža koja odgovara kao produkcijski backend. */
function examPage(overrides = {}) {
  const page = loadPage();
  const responses = Object.assign({ start: READY, submit: GRADED }, overrides);
  page.network.responder = async (entry) => {
    if (entry.url.indexOf('/exam/start') !== -1) {
      const body = responses.start;
      if (body instanceof Error) throw body;
      return jsonResponse(body, responses.startStatus || 200);
    }
    if (entry.url.indexOf('/exam/submit') !== -1) {
      return jsonResponse(responses.submit, responses.submitStatus || 200);
    }
    return jsonResponse({}, 200);
  };
  return page;
}

const examCalls = page => page.network.requests.filter(r => r.url.indexOf('/exam/') !== -1);

async function openExam(page, overrides) {
  page.ui.enterExam('Razlomci', '6-04');
  await settle(8);
  return page;
}

async function answerAll(page) {
  for (let i = 0; i < 5; i += 1) {
    page.ui.renderExamQuestion(i);
    const options = page.ui.examEls.options.children;
    options[i === 2 ? 1 : 0].dispatch('click');
  }
}

// ---------------------------------------------------------------------------
// CSS UGOVOR — mehanizam koji preklapanje čini nemogućim
// ---------------------------------------------------------------------------

test('CSS ugovor: sekcije su skrivene po pravilu, ne po atributu hidden', () => {
  assert.ok(INDEX_HTML.includes('.exam-card [data-exam-state]{display:none;}'),
    'nedostaje pravilo koje SVE sekcije skriva podrazumijevano');
  ['generating', 'question', 'submitting', 'result', 'error'].forEach(state => {
    assert.ok(
      INDEX_HTML.includes('.exam-card[data-state="' + state + '"] [data-exam-state="' + state + '"]'),
      'nedostaje pravilo prikaza za stanje ' + state);
  });
  // Stari mehanizam (koji je i pao) ne smije se vratiti.
  assert.ok(!INDEX_HTML.includes('function examShow('), 'stari examShow() je i dalje prisutan');
});

test('CSS ugovor: kontrolni ne pravi ugniježđen scroll', () => {
  assert.ok(INDEX_HTML.includes('body.exam-open main{height:auto;min-height:100dvh;}'),
    'kontrolni ne prelazi u dokumentni tok');
  // Komentari se uklanjaju: mjeri se STVARNI CSS, ne tekst obrazloženja.
  const examCss = INDEX_HTML
    .slice(INDEX_HTML.indexOf('.exam-card{'), INDEX_HTML.indexOf('/* Phase 2: mala preporuka'))
    .replace(/\/\*[\s\S]*?\*\//g, '');
  assert.ok(!/overflow-y:auto/.test(examCss),
    'kontrolni i dalje ima vlastiti vertikalni scroll kontejner');
  // Dozvoljen je SAMO horizontalni scroll same formule (duga jednačina), nikad
  // cijele stranice — vidi zahtjev o mobilnom prelijevanju.
  assert.ok(/mjx-container\[display="true"\]\{overflow-x:auto/.test(examCss));
});

// ---------------------------------------------------------------------------
// STANJA: prelazi i međusobna isključivost
// ---------------------------------------------------------------------------

test('ulazak u kontrolni: GENERATING pa QUESTION, bez /chat poziva', async () => {
  const page = await openExam(examPage());
  assert.strictEqual(page.ui.examState(), 'question');
  assert.deepStrictEqual(examCalls(page).map(r => r.url),
    ['/api/ai-tutor/exam/start']);
  assert.ok(!page.network.requests.some(r => r.url.indexOf('/chat') !== -1),
    'kontrolni ne smije dirati generički /chat');
  assert.ok(page.doc.body.classList.contains('exam-open'));
});

test('stanje je JEDNA vrijednost — rezultat ne može koegzistirati s loaderom i greškom', async () => {
  const page = await openExam(examPage());
  await answerAll(page);
  await page.ui.submitExam();
  await settle(8);
  assert.strictEqual(page.ui.examState(), 'result');
  // Tačna reprodukcija produkcijskog screenshota: pokušaj da uz rezultat
  // „ostane“ i staro stanje. Atribut ne može nositi dvije vrijednosti.
  page.ui.setExamState('generating');
  assert.strictEqual(page.ui.examState(), 'generating');
  page.ui.setExamState('error');
  assert.strictEqual(page.ui.examState(), 'error');
  page.ui.setExamState('result');
  assert.strictEqual(page.ui.examState(), 'result');
  assert.strictEqual(page.ui.examCard.dataset.state, 'result');
});

test('neuspjelo generisanje daje SAMO stanje greške', async () => {
  const page = await openExam(examPage({
    start: { status: 'failed', message: 'Nismo uspjeli pripremiti test.' } }));
  assert.strictEqual(page.ui.examState(), 'error');
  assert.match(page.ui.examEls.errorMsg.textContent, /Nismo uspjeli pripremiti test/);
});

test('Pokušaj ponovo iz greške vodi u novo generisanje i uspije', async () => {
  const page = loadPage();
  let attempt = 0;
  page.network.responder = async (entry) => {
    if (entry.url.indexOf('/exam/start') !== -1) {
      attempt += 1;
      return jsonResponse(attempt === 1
        ? { status: 'failed', message: 'Nismo uspjeli pripremiti test.' } : READY, 200);
    }
    return jsonResponse({}, 200);
  };
  page.ui.enterExam('Razlomci', '6-04');
  await settle(8);
  assert.strictEqual(page.ui.examState(), 'error');
  page.ui.examEls.retry.dispatch('click');
  await settle(8);
  assert.strictEqual(page.ui.examState(), 'question');
  assert.strictEqual(attempt, 2);
});

// ---------------------------------------------------------------------------
// TOK PITANJA
// ---------------------------------------------------------------------------

test('pet pitanja, četiri opcije, izbor se mijenja, navigacija radi', async () => {
  const page = await openExam(examPage());
  assert.strictEqual(page.ui.exam.questions.length, 5);
  assert.strictEqual(page.ui.examEls.options.children.length, 4);
  assert.match(page.ui.examEls.progress.textContent, /^Pitanje 1 od 5$/);
  assert.strictEqual(page.ui.examEls.prev.disabled, true);
  assert.strictEqual(page.ui.examEls.finish.hidden, true);

  page.ui.examEls.options.children[0].dispatch('click');
  assert.strictEqual(page.ui.exam.answers.q1, 'a');
  page.ui.examEls.options.children[2].dispatch('click');   // promjena izbora
  assert.strictEqual(page.ui.exam.answers.q1, 'c');
  const selected = page.ui.examEls.options.children.filter(
    c => c.classList.contains('selected'));
  assert.strictEqual(selected.length, 1, 'tačno jedna opcija smije biti označena');
  assert.strictEqual(selected[0].getAttribute('aria-pressed'), 'true');

  page.ui.examEls.next.dispatch('click');
  assert.match(page.ui.examEls.progress.textContent, /^Pitanje 2 od 5$/);
  page.ui.examEls.prev.dispatch('click');
  assert.match(page.ui.examEls.progress.textContent, /^Pitanje 1 od 5$/);

  page.ui.renderExamQuestion(4);
  assert.strictEqual(page.ui.examEls.next.hidden, true);
  assert.strictEqual(page.ui.examEls.finish.hidden, false);
  assert.strictEqual(page.ui.examEls.dots.children.length, 5);
});

test('nepotpun test se ne predaje — ostaje na pitanjima, bez mrežnog poziva', async () => {
  const page = await openExam(examPage());
  page.ui.renderExamQuestion(0);
  page.ui.examEls.options.children[0].dispatch('click');   // samo jedno pitanje
  await page.ui.submitExam();
  await settle(4);
  assert.strictEqual(page.ui.examState(), 'question');
  assert.strictEqual(examCalls(page).filter(r => r.url.indexOf('submit') !== -1).length, 0);
});

// ---------------------------------------------------------------------------
// REZULTAT
// ---------------------------------------------------------------------------

test('predaja zove /exam/submit i prikazuje kompaktan rezultat', async () => {
  const page = await openExam(examPage());
  await answerAll(page);
  await page.ui.submitExam();
  await settle(8);
  const submit = examCalls(page).find(r => r.url.indexOf('submit') !== -1);
  assert.ok(submit, 'predaja nije poslana');
  assert.strictEqual(submit.body.exam_id, 'EXAM-1');
  assert.strictEqual(Object.keys(submit.body.answers).length, 5);
  assert.strictEqual(page.ui.examState(), 'result');
  assert.strictEqual(page.ui.examEls.score.textContent, '4/5');
  assert.strictEqual(page.ui.examEls.scorePct.textContent, '80%');
  assert.match(page.ui.examEls.recommend.innerHTML, /Vrlo dobro\./);
  assert.match(page.ui.examEls.recommend.innerHTML, /Ponovi ove lekcije/);
  assert.match(page.ui.examEls.recommend.innerHTML, /Lekcija 3/);
  assert.strictEqual(page.ui.examEls.resultList.children.length, 5);
});

test('5/5 ne prikazuje praznu sekciju „ponovi ove lekcije“', async () => {
  const perfect = Object.assign({}, GRADED, {
    score: 5, percentage: 100,
    questions: GRADED.questions.map(q => Object.assign({}, q, { correct: true })),
    recommendation: { lessons: [], message: 'Odlično — spreman/na si za ovu oblast.' },
  });
  const page = await openExam(examPage({ submit: perfect }));
  await answerAll(page);
  await page.ui.submitExam();
  await settle(8);
  assert.match(page.ui.examEls.recommend.innerHTML, /Odlično/);
  assert.ok(!/Ponovi ove lekcije/.test(page.ui.examEls.recommend.innerHTML));
});

test('dugmad težine: primarno Novi test, klema na krajevima ljestvice', async () => {
  const page = await openExam(examPage({
    start: Object.assign({}, READY, { difficulty: 'easier' }) }));
  await answerAll(page);
  await page.ui.submitExam();
  await settle(8);
  assert.strictEqual(page.ui.examEls.easier.disabled, true, 'na dnu ljestvice Lakši je onemogućen');
  assert.strictEqual(page.ui.examEls.harder.disabled, false);
  assert.strictEqual(page.ui.examEls.same.disabled, false);
});

test('novi test iz rezultata briše stari rezultat i traži novo generisanje', async () => {
  const page = loadPage();
  let started = 0;
  let pendingStart = null;
  page.network.responder = async (entry) => {
    if (entry.url.indexOf('/exam/start') !== -1) {
      started += 1;
      if (started === 1) return jsonResponse(READY, 200);
      // Drugi start ostaje NERAZRIJEŠEN da se stanje uhvati baš u GENERATING.
      return new Promise(resolve => { pendingStart = resolve; });
    }
    return jsonResponse(GRADED, 200);
  };
  page.ui.enterExam('Razlomci', '6-04');
  await settle(8);
  await answerAll(page);
  await page.ui.submitExam();
  await settle(8);
  assert.strictEqual(page.ui.examState(), 'result');

  page.ui.examEls.harder.dispatch('click');
  await settle(4);
  assert.strictEqual(page.ui.examState(), 'generating',
    'dok se novi test generiše, stari rezultat NE smije ostati stanje ekrana');
  assert.strictEqual(page.ui.examEls.resultList.children.length, 0,
    'stari rezultat je morao biti obrisan iz DOM-a');
  assert.strictEqual(page.ui.examEls.recommend.innerHTML, '');
  assert.strictEqual(page.ui.exam.questions.length, 0);
  assert.strictEqual(started, 2);
  const lastStart = examCalls(page).filter(r => r.url.indexOf('start') !== -1).pop();
  assert.strictEqual(lastStart.body.relative, 'harder');
  if (pendingStart) pendingStart(jsonResponse(READY, 200));
});

test('izlaz iz kontrolnog vraća stranicu u normalan tok', async () => {
  const page = await openExam(examPage());
  page.ui.examEls.back.dispatch('click');
  assert.strictEqual(page.ui.examCard.hidden, true);
  assert.ok(!page.doc.body.classList.contains('exam-open'));
});
