/* STRUKTURA MCQ KARTICE ZA MJEŠOVIT TEKST (proza + $…$).
 *
 * ŽIVI PREDIZDANJE NALAZ: objavljena opcija `$x = 0$ ili $x = 3$` čitala se u
 * pregledaču kao „x = 0ilix = 3“. Tekst je bio ispravan na serveru, u
 * mathsafe, u normalizaciji terminologije i u payloadu — kvar je nastajao TEK
 * u layoutu: `.mc-option-card` je `display:flex`, MathJax `$…$` zamijeni
 * `mjx-container` elementima, pa proza između njih („ ili “) postane zaseban
 * ANONIMAN flex element. Razmaci na krajevima anonimnog flex elementa se po
 * specifikaciji flexboxa ne prikazuju, pa riječ sraste uz obje formule.
 *
 * GRANICA OVOG TESTA (izričito): `tests/frontend/browser_stub.js` nije pravi
 * pregledač — nema CSS-a, layouta ni MathJaxa. Vizuelni razmak se ovdje NE
 * mjeri i ovaj test ga ne dokazuje. Dokazuje se STRUKTURNA INVARIJANTA koja
 * kvar čini nemogućim: cio tekst opcije živi u TAČNO JEDNOM potomku kartice,
 * pa kartica ima jedan flex element i unutar njega vrijede obična inline
 * pravila za razmake — bez obzira na to koliko `mjx-container` elemenata
 * MathJax kasnije napravi.
 *
 * FALSIFIKACIJA: uklanjanje `.mc-option-text` omotača iz `buildOptionCards`
 * (povratak na `b.innerHTML = escapeHtml(...)`) obara `optionText(card)` na
 * `null` i svaki `assert` ispod pada. Test dakle ovisi BAŠ o promijenjenom
 * mehanizmu, a ne o nekoj drugoj normalizaciji.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { loadPage, optionText } = require('./browser_stub.js');

const TASK = 'Riješi jednačinu: $4x^{2} - 12x = 0$';

/* DOSLOVNO tekstovi koje deterministički generator objavljuje za ovu porodicu
 * (matbot/deterministic/equations.py::_quadratic_package, oblik factor_out_x)
 * i kontrolni oblici mješovite proze/matematike iz drugih porodica. */
const MIXED_OPTIONS = [
  { id: 'a', text: '$x = 0$ ili $x = 3$' },
  { id: 'b', text: '$x = 3$' },
  { id: 'c', text: '$x = 0$ ili $x = -3$' },
  { id: 'd', text: '$x = 0$' },
];

/* `escaped` je ono što omotač STVARNO nosi: `escapeHtml` (zatečeno ponašanje,
 * ne dira se) pretvara `>` u `&gt;`. Ovdje se očekuje baš taj oblik da test
 * ne bi lažno tvrdio nešto o HTML escapeovanju — mjeri se samo da omotač
 * prenosi cio tekst, sa svim razmacima oko proze. */
const CONTROL_OPTIONS = [
  { id: 'a', text: '$x = 2$ i $y = 3$', escaped: '$x = 2$ i $y = 3$' },
  { id: 'b', text: '$5$ cm', escaped: '$5$ cm' },
  { id: 'c', text: 'Da, jer je $x > 0$.', escaped: 'Da, jer je $x &gt; 0$.' },
  { id: 'd', text: '90', escaped: '90' },
];

function practicePage(options) {
  const page = loadPage();
  page.ui.state.mode = 'practice';
  page.ui.state.grade = 9;
  page.ui.state.topic = '9-06-013';
  // Kartice pravi STVARNI kod stranice, uključujući omotač teksta.
  page.ui.buildOptionCards(options, TASK, 'identity-1');
  return page;
}

function cards(page) {
  return page.ui.optionsBox.querySelectorAll('.mc-option-card');
}

test('mixed prose/math option lives in exactly one flex child of the card', () => {
  const page = practicePage(MIXED_OPTIONS);
  const built = cards(page);
  assert.strictEqual(built.length, 4);
  for (const card of built) {
    assert.strictEqual(card.children.length, 1,
      'kartica mora imati TAČNO JEDAN flex element — više njih vraća kvar '
      + '„x = 0ilix = 3“ čim MathJax razbije $…$');
    assert.ok(card.children[0].classList.contains('mc-option-text'),
      'jedini potomak je omotač teksta opcije');
    assert.strictEqual(card.innerHTML, '',
      'tekst ne smije stajati direktno na dugmetu: tada bi proza između dva '
      + 'mjx-containera postala anoniman flex element');
  }
});

test('option wrapper preserves the prose separator around math verbatim', () => {
  const page = practicePage(MIXED_OPTIONS);
  assert.deepStrictEqual(cards(page).map(optionText),
    MIXED_OPTIONS.map(o => o.text));
  // Razmaci oko proze između dvije formule moraju preživjeti neokrnjeni —
  // upravo njih je flex layout ranije progutao.
  assert.ok(optionText(cards(page)[0]).includes('$ ili $'),
    'razmaci oko „ili“ moraju ostati u tekstu opcije');
});

test('control mixed forms keep their text intact in one wrapper', () => {
  const page = practicePage(CONTROL_OPTIONS);
  const built = cards(page);
  assert.deepStrictEqual(built.map(optionText), CONTROL_OPTIONS.map(o => o.escaped));
  for (const card of built) assert.strictEqual(card.children.length, 1);
  // Razmak oko proze između formula preživi i u ostalim mješovitim oblicima.
  assert.ok(optionText(built[0]).includes('$ i $'));
  assert.ok(optionText(built[1]).includes('$ cm'));
});

test('wrapper does not change option identity, order or clickability', async () => {
  const page = practicePage(MIXED_OPTIONS);
  const built = cards(page);
  // Redoslijed i ID-jevi su SERVERSKI — omotač ih ne smije dodirnuti.
  assert.deepStrictEqual(built.map(b => b.dataset.optionId), ['a', 'b', 'c', 'd']);
  // Rukovalac klika i dalje stoji na DUGMETU, ne na omotaču.
  page.network.responder = async () => { throw new Error('no network in this test'); };
  const fired = built[0].dispatch('click');
  assert.strictEqual(fired.length, 1, 'klik na karticu i dalje pokreće tačno jedan rukovalac');
  // Stanje kartica (tačno/netačno/onemogućeno) ostaje na dugmetu.
  built[0].classList.add('correct');
  assert.ok(built[0].classList.contains('correct'));
  assert.ok(!built[0].children[0].classList.contains('correct'));
});
