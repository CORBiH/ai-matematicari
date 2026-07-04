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
  '1. korak',
  '', '', '',
  'Kraj',
].join('\n');
const out = renderTutorHTML(sample);

const checks = {
  h3_no_raw_markdown: out.includes('<h3>Tema</h3>') && !out.includes('###'),
  bold: out.includes('<strong>boldom</strong>'),
  ul: out.includes('<ul><li>prva</li><li>druga</li></ul>'),
  ol: out.includes('<ol><li>korak</li></ol>'),
  xss_escaped: !out.includes('<script>') && out.includes('&lt;script&gt;'),
  dot_lines_removed: !out.includes('<br>.<br>'),
  br_collapsed: !out.includes('<br><br><br>'),
};
const failed = Object.entries(checks).filter(([, ok]) => !ok).map(([k]) => k);
if (failed.length) {
  console.error('RENDERER FAILED:', failed.join(', '));
  console.error(out);
  process.exit(1);
}
console.log('renderer checks OK (' + Object.keys(checks).length + ' provjera)');
