// MAT-BOT — provjera inline JS-a iz templates/index.html (bez framework-a).
// Pokretanje:  node scripts/check_js.mjs
// 1) sintaksna provjera najvećeg <script> bloka
// 2) behavior provjere renderTutorHTML (naslovi, bold, liste, XSS escape, "." linije)
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const html = readFileSync(path.join(root, 'templates', 'index.html'), 'utf-8');

const blocks = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
if (!blocks.length) { console.error('FAILED: nema <script> blokova'); process.exit(1); }
const main = blocks.reduce((a, b) => (b.length > a.length ? b : a), '');

// 1) sintaksa (kompajlira bez izvršavanja — DOM se ne dira)
try {
  new Function(main);
  console.log(`syntax OK (${main.length} chars)`);
} catch (e) {
  console.error('SYNTAX FAILED:', e.message);
  process.exit(1);
}

// 2) renderTutorHTML behavior
const fnMatch = main.match(/function renderTutorHTML\(raw\)\{[\s\S]*?\n    \}/);
if (!fnMatch) { console.error('FAILED: renderTutorHTML nije pronađen'); process.exit(1); }

const escapeHtml = s => (s || '').replace(/[&<>"']/g,
  m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
const renderTutorHTML = new Function('escapeHtml', fnMatch[0] + '; return renderTutorHTML;')(escapeHtml);

const sample = [
  '### Tema',
  'Tekst sa **boldom** i <script>alert(1)</script>.',
  '.',
  '- prva',
  '- druga',
  '1. korak A',
  '',
  '1. korak B',
  '1. korak C',
  '', '', '',
  'Ako je $$6|12$$ i $$6 \\mid 18$$, vrijedi.',
  '$$5 - 1 = 4$$',
  '$$z = a_1 + a_2 + a_3 + a_4 + a_5 + a_6 + a_7 + a_8 = 360$$',
  'Kraj',
].join('\n');
const out = renderTutorHTML(sample);

const checks = {
  h3_no_raw_markdown: out.includes('<h3>Tema</h3>') && !out.includes('###'),
  bold: out.includes('<strong>boldom</strong>'),
  ul: out.includes('<ul><li>prva</li><li>druga</li></ul>'),
  // ponovljene "1." stavke razdvojene praznim redom → JEDNA <ol> (browser broji 1,2)
  ol_merged: out.includes('<ol><li>korak A</li><li>korak B</li><li>korak C</li></ol>'),
  xss_escaped: !out.includes('<script>') && out.includes('&lt;script&gt;'),
  dot_lines_removed: !out.includes('<br>.<br>'),
  // agresivnije sažimanje praznih redova: nikad dupli <br>
  br_collapsed: !out.includes('<br><br>'),
  // kratki display blokovi → inline \(...\) (i usred rečenice i sami u redu)
  short_display_inline:
    out.includes('\\(6|12\\)') && out.includes('\\(6 \\mid 18\\)') &&
    out.includes('\\(5 - 1 = 4\\)') && !out.includes('$$6|12$$') && !out.includes('$$5 - 1 = 4$$'),
  // dugi display blok (>40 znakova) OSTAJE $$...$$
  long_display_preserved: out.includes('$$z = a_1 + a_2 + a_3 + a_4 + a_5 + a_6 + a_7 + a_8 = 360$$'),
};
const failed = Object.entries(checks).filter(([, ok]) => !ok).map(([k]) => k);
if (failed.length) {
  console.error('RENDERER FAILED:', failed.join(', '));
  console.error(out);
  process.exit(1);
}
console.log('renderer checks OK (' + Object.keys(checks).length + ' provjera)');

// 3) isFollowupMessage behavior (kratke potvrde → follow-up, konkretno → ne)
const fuMatch = main.match(/function isFollowupMessage\(t\)\{[\s\S]*?\n    \}/);
if (!fuMatch) { console.error('FAILED: isFollowupMessage nije pronađen'); process.exit(1); }
const isFollowupMessage = new Function(fuMatch[0] + '; return isFollowupMessage;')();

const fuChecks = {
  moze: isFollowupMessage('moze'),
  moze_dijakritika: isFollowupMessage('Može!'),
  hocu: isFollowupMessage('hocu') && isFollowupMessage('hoću'),
  da: isFollowupMessage('da'),
  nastavi: isFollowupMessage('nastavi'),
  dalje: isFollowupMessage('dalje'),
  jos: isFollowupMessage('jos') && isFollowupMessage('još'),
  moze_primjer: isFollowupMessage('može primjer'),
  daj_primjer: isFollowupMessage('daj primjer'),
  konkretno_pitanje_nije: !isFollowupMessage('koliko je 5-1'),
  broj_nije: !isFollowupMessage('24'),
  duga_poruka_nije: !isFollowupMessage('može li mi neko objasniti kako se računa NZS'),
  prazno_nije: !isFollowupMessage(''),
};
const fuFailed = Object.entries(fuChecks).filter(([, ok]) => !ok).map(([k]) => k);
if (fuFailed.length) {
  console.error('FOLLOWUP FAILED:', fuFailed.join(', '));
  process.exit(1);
}
console.log('followup checks OK (' + Object.keys(fuChecks).length + ' provjera)');

// 4) image follow-up helper
const imageFuMatch = main.match(/function foldTutorText\(t\)\{[\s\S]*?function isImageFollowupMessage\(t\)\{[\s\S]*?\n    \}/);
if (!imageFuMatch) { console.error('FAILED: isImageFollowupMessage nije pronađen'); process.exit(1); }
const imageFns = new Function(imageFuMatch[0] + '; return { isImageFollowupMessage };')();
const imageChecks = {
  prvi: imageFns.isImageFollowupMessage('objasni prvi zadatak'),
  kako_prvi: imageFns.isImageFollowupMessage('kako si uradio prvi'),
  rezultat_2: imageFns.isImageFollowupMessage('kako si dobio rezultat za 2.'),
  slika: imageFns.isImageFollowupMessage('ne razumijem zadatak sa slike'),
  unrelated_math_not_image: !imageFns.isImageFollowupMessage('koliko je 5-1'),
  unrelated_text_not_image: !imageFns.isImageFollowupMessage('može li primjer iz razlomaka'),
};
const imageFailed = Object.entries(imageChecks).filter(([, ok]) => !ok).map(([k]) => k);
if (imageFailed.length) {
  console.error('IMAGE FOLLOWUP FAILED:', imageFailed.join(', '));
  process.exit(1);
}
console.log('image followup checks OK (' + Object.keys(imageChecks).length + ' provjera)');

// 5) practice task-state helpers
const practiceMatch = main.match(/function foldTutorText\(t\)\{[\s\S]*?function looksLikeTutorTask\(t\)\{[\s\S]*?\n    \}/);
if (!practiceMatch) { console.error('FAILED: practice state helperi nisu pronađeni'); process.exit(1); }
const practiceFns = new Function(
  practiceMatch[0] +
  '; return { isNewPracticeTaskRequest, isShortPracticeAnswer, extractTutorTask, looksLikeTutorTask };'
)();

const comparisonTask = 'Uporedi brojeve: 7 205 i 7 250. Koji je veći broj? Koristi znakove <, > ili =.';
const practiceChecks = {
  extract_comparison_task: practiceFns.extractTutorTask(null, comparisonTask) === comparisonTask,
  structured_task_preferred:
    practiceFns.extractTutorTask({ last_tutor_task: 'Odredi prethodnik broja 1 000.' }, comparisonTask) ===
    'Odredi prethodnik broja 1 000.',
  comparison_task_detected: practiceFns.looksLikeTutorTask(comparisonTask),
  ne_znam_answer: practiceFns.isShortPracticeAnswer('ne znam'),
  help_answers: practiceFns.isShortPracticeAnswer('pomozi') &&
    practiceFns.isShortPracticeAnswer('daj hint') &&
    practiceFns.isShortPracticeAnswer('objasni'),
  spaced_number_answer: practiceFns.isShortPracticeAnswer('7 250'),
  fraction_answer: practiceFns.isShortPracticeAnswer('5/18'),
  new_task_detected: practiceFns.isNewPracticeTaskRequest('daj mi novi zadatak'),
  new_task_not_answer: !practiceFns.isShortPracticeAnswer('daj mi novi zadatak'),
  hint_not_new_task: !practiceFns.isNewPracticeTaskRequest('daj hint'),
};
const practiceFailed = Object.entries(practiceChecks).filter(([, ok]) => !ok).map(([k]) => k);
if (practiceFailed.length) {
  console.error('PRACTICE STATE FAILED:', practiceFailed.join(', '));
  process.exit(1);
}
console.log('practice state checks OK (' + Object.keys(practiceChecks).length + ' provjera)');
