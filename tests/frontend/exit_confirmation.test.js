/* „Zatvori“ → potvrda → „Izađi“: DOM-nivo test STVARNIH rukovalaca.
 *
 * ZAŠTO POSTOJI: „Izađi“ je jedina radnja koja učenika izvodi IZ aplikacije, i
 * jedina koja komunicira s roditeljskom (Thinkific) stranicom. Statička
 * provjera markupa ne bi dokazala ono što je ovdje bitno: da klik na „Zatvori“
 * NE izlazi, da „Ostani“/Escape ne izlaze, i da dvostruki klik ne objavi dvije
 * poruke.
 *
 * GRANICA: vidi tests/frontend/browser_stub.js — nema layouta, CSS-a ni
 * bubblinga. Zato se delegirani `data-close` klik isporučuje na dokument s
 * eksplicitnim `target`-om, tačno kako ga pregledač isporuči nakon bubblinga.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { loadPage } = require('./browser_stub.js');

const CLOSE_MESSAGE = 'matbot:close';

/** Stub ne parsira markup — elemente kreira tek na zahtjev po ID-u, bez klasa i
 *  atributa. Dijalog potvrde se zato ovdje sastavlja tačno onako kako stoji u
 *  templates/index.html: klasa `modal` (po njoj `closeAllModals` bira šta gasi)
 *  i `data-close` na „Ostani“ (po njemu radi delegirani rukovalac).
 *  Da ova rekonstrukcija ne bi tiho odlutala od markupa, iste te atribute
 *  statički zaključava tests/test_frontend_header_ui.py. */
function withDialogNode(page) {
  const dialog = page.doc.getElementById('confirm-exit');
  dialog.classList.add('modal');
  page.doc.getElementById('stayInMatbot').setAttribute('data-close', '#confirm-exit');
  return dialog;
}

function closeMessages(page) {
  return page.parentMessages.filter(m => m.message && m.message.type === CLOSE_MESSAGE);
}

test('„Zatvori“ otvara potvrdu i NE izlazi iz MAT-BOT-a', () => {
  const page = loadPage();
  const dialog = withDialogNode(page);

  page.doc.getElementById('tutorExitBtn').click();

  assert.equal(dialog.style.display, 'flex', 'dijalog potvrde mora biti otvoren');
  assert.equal(page.doc.getElementById('overlay').style.display, 'block');
  assert.equal(closeMessages(page).length, 0, 'klik na „Zatvori“ ne smije izaći');
});

test('„Ostani“ zatvara potvrdu i ostavlja učenika u MAT-BOT-u', () => {
  const page = loadPage();
  const dialog = withDialogNode(page);

  page.doc.getElementById('tutorExitBtn').click();
  // `data-close` je delegiran na dokument — isporuči kako pregledač isporuči.
  page.doc.dispatch('click', { target: page.doc.getElementById('stayInMatbot') });

  assert.notEqual(dialog.style.display, 'flex', 'dijalog mora biti zatvoren');
  assert.equal(page.doc.getElementById('overlay').style.display, 'none');
  assert.equal(closeMessages(page).length, 0);
});

test('Escape zatvara potvrdu bez izlaska', () => {
  const page = loadPage();
  const dialog = withDialogNode(page);

  page.doc.getElementById('tutorExitBtn').click();
  page.doc.dispatch('keydown', { key: 'Escape' });

  assert.notEqual(dialog.style.display, 'flex');
  assert.equal(closeMessages(page).length, 0, 'Escape nikad ne pokreće radnju dijaloga');
});

test('„Izađi“ objavljuje TAČNO jednu matbot:close poruku roditelju', () => {
  const page = loadPage();
  withDialogNode(page);

  page.doc.getElementById('tutorExitBtn').click();
  page.doc.getElementById('doExitMatbot').click();

  const sent = closeMessages(page);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].message.source, 'matbot');
  // Poruka ne smije nositi NIJEDAN podatak učenika — samo zatvorenu konstantu.
  assert.deepEqual(Object.keys(sent[0].message).sort(), ['source', 'type']);
});

test('dvostruki klik na „Izađi“ ne objavljuje dvije poruke', () => {
  const page = loadPage();
  withDialogNode(page);

  page.doc.getElementById('tutorExitBtn').click();
  const exit = page.doc.getElementById('doExitMatbot');
  exit.click();
  exit.click();
  exit.click();

  assert.equal(closeMessages(page).length, 1);
});

test('bez roditeljskog prozora izlazak pada TIHO, bez izuzetka', () => {
  const page = loadPage({ parent: false });
  withDialogNode(page);

  page.doc.getElementById('tutorExitBtn').click();
  assert.doesNotThrow(() => page.doc.getElementById('doExitMatbot').click());
  assert.equal(page.parentMessages.length, 0);
});

test('„Zatvori“ na ekranu kontrolnog vodi u ISTU potvrdu', () => {
  const page = loadPage();
  const dialog = withDialogNode(page);

  page.doc.getElementById('examExitBtn').click();

  assert.equal(dialog.style.display, 'flex');
  assert.equal(closeMessages(page).length, 0);
});

test('„Zatvori“ na početnom ekranu vodi u ISTU potvrdu', () => {
  const page = loadPage();
  const dialog = withDialogNode(page);

  page.doc.getElementById('homeExitBtn').click();

  assert.equal(dialog.style.display, 'flex');
  assert.equal(closeMessages(page).length, 0, 'ni na početnom ekranu se ne izlazi bez potvrde');
});

test('sva tri ekrana dijele JEDNU implementaciju izlaska', () => {
  // Isti dijalog, isti `matbot:close`, isti `exitInFlight` — dokazano tako što
  // izlazak zatražen s BILO KOJEG ekrana ostane na tačno jednoj poruci.
  const page = loadPage();
  withDialogNode(page);

  for (const id of ['homeExitBtn', 'tutorExitBtn', 'examExitBtn']) {
    page.doc.getElementById(id).click();
  }
  page.doc.getElementById('doExitMatbot').click();

  assert.equal(closeMessages(page).length, 1);
});

test('„Nazad“ i dalje ne izlazi iz MAT-BOT-a', () => {
  const page = loadPage();

  page.doc.getElementById('tutorBackBtn').click();
  page.doc.getElementById('examBackBtn').click();

  assert.equal(page.parentMessages.length, 0,
    'navigacija unutar aplikacije ne smije dodirnuti roditeljski prozor');
});
