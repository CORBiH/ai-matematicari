'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const { Doc, Element, settle } = require('./browser_stub.js');

const TEMPLATE = path.join(__dirname, '..', '..', 'templates', 'admin_registry.html');

function add(doc, parent, tag, attributes = {}) {
  const node = new Element(tag, doc);
  for (const [name, value] of Object.entries(attributes)) {
    if (name === 'class') node.className = value;
    else if (name === 'checked') node.checked = value;
    else if (name === 'value') node.value = String(value);
    else node.setAttribute(name, value);
  }
  parent.appendChild(node);
  return node;
}

function fixture(ids = ['35', '36']) {
  const doc = new Doc();
  const form = add(doc, doc.body, 'form', { id: 'bulk-profile-form' });
  const selectAll = add(doc, form, 'input', { id: 'select-visible', type: 'checkbox' });
  const area = add(doc, form, 'div', { id: 'bulk-actions', class: 'bulk-actions' });
  const count = add(doc, area, 'span', { id: 'selected-count' });
  const bulkButton = add(doc, area, 'button', { type: 'submit' });
  const typeButton = add(doc, area, 'button', {
    type: 'submit', formaction: '/admin/students/bulk-account-type',
  });
  const rows = ids.map(id => {
    const row = add(doc, form, 'div', { 'data-student-row': id });
    const selected = add(doc, row, 'input', {
      class: 'row-select', type: 'checkbox', name: 'student_ids', value: id,
    });
    const account = add(doc, row, 'select', {
      class: 'account-type', 'data-student-id': id, name: `account_type_${id}`,
      value: 'STUDENT',
    });
    const grades = add(doc, row, 'div', {
      class: 'grade-checks', 'data-grades-for': id,
    });
    const gradeInputs = [6, 7, 8, 9].map(grade => add(doc, grades, 'input', {
      type: 'checkbox', name: `grades_${id}`, value: grade,
    }));
    const save = add(doc, row, 'button', {
      type: 'submit', formaction: `/admin/students/${id}/grade`,
    });
    return { id, row, selected, account, grades, gradeInputs, save };
  });

  const html = fs.readFileSync(TEMPLATE, 'utf8');
  const blocks = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)];
  assert.equal(blocks.length, 1, 'admin registry must have one inline behavior block');
  const win = { setTimeout };
  const sandbox = { document: doc, window: win, setTimeout, Array };
  win.document = doc;
  vm.runInNewContext(blocks[0][1], sandbox, { filename: TEMPLATE });
  return { doc, form, selectAll, area, count, bulkButton, typeButton, rows, sandbox };
}

function selectedStudentIds(page) {
  return page.form.querySelectorAll('.row-select')
    .filter(input => input.checked && !input.disabled)
    .map(input => input.value);
}

test('row checkboxes and select-all operate only on rendered rows', () => {
  const page = fixture(['35', '36']);
  const notRendered = new Element('input', page.doc);
  notRendered.className = 'row-select';
  notRendered.value = '37';

  page.rows[0].selected.checked = true;
  page.rows[0].selected.dispatch('change');
  assert.equal(page.count.textContent, '1');
  assert.equal(page.area.classList.contains('is-visible'), true);
  assert.equal(page.selectAll.indeterminate, true);
  assert.deepEqual(selectedStudentIds(page), ['35']);

  page.selectAll.checked = true;
  page.selectAll.dispatch('change');
  assert.deepEqual(selectedStudentIds(page), ['35', '36']);
  assert.equal(notRendered.checked, false);
  assert.equal(page.selectAll.indeterminate, false);
});

test('unselected rows remain absent from a filtered bulk submission', () => {
  const page = fixture(['35', '39']);
  page.rows[1].selected.checked = true;
  page.rows[1].selected.dispatch('change');

  assert.deepEqual(selectedStudentIds(page), ['39']);
  assert.equal(page.rows[0].selected.checked, false);

  const filtered = fixture(['39']);
  filtered.selectAll.checked = true;
  filtered.selectAll.dispatch('change');
  assert.deepEqual(selectedStudentIds(filtered), ['39']);
});

test('each row independently accepts one or two grades and rejects a third', () => {
  const page = fixture();
  const first = page.rows[0];
  const second = page.rows[1];

  first.gradeInputs[0].checked = true;
  first.grades.dispatch('change', { target: first.gradeInputs[0] });
  assert.deepEqual(first.gradeInputs.map(input => input.checked), [true, false, false, false]);
  assert.deepEqual(second.gradeInputs.map(input => input.checked), [false, false, false, false]);

  first.gradeInputs[1].checked = true;
  first.grades.dispatch('change', { target: first.gradeInputs[1] });
  assert.deepEqual(first.gradeInputs.map(input => input.checked), [true, true, false, false]);

  first.gradeInputs[2].checked = true;
  first.grades.dispatch('change', { target: first.gradeInputs[2] });
  assert.deepEqual(first.gradeInputs.map(input => input.checked), [true, true, false, false]);

  second.gradeInputs[3].checked = true;
  second.grades.dispatch('change', { target: second.gradeInputs[3] });
  assert.deepEqual(second.gradeInputs.map(input => input.checked), [false, false, false, true]);
});

test('SUPPORT and TEST disable grade controls and STUDENT restores them', () => {
  const page = fixture(['35']);
  const row = page.rows[0];
  for (const accountType of ['SUPPORT', 'TEST']) {
    row.account.value = accountType;
    row.account.dispatch('change');
    assert.equal(row.gradeInputs.every(input => input.disabled), true);
  }
  row.account.value = 'STUDENT';
  row.account.dispatch('change');
  assert.equal(row.gradeInputs.every(input => !input.disabled), true);
});

test('bulk submit enters disabled loading state and single-row action remains usable', async () => {
  const page = fixture(['35']);
  assert.equal(page.rows[0].save.getAttribute('formaction'), '/admin/students/35/grade');
  assert.equal(page.sandbox.matbotBulkSubmit(page.form), true);
  await settle(2);

  const submitters = page.form.querySelectorAll('button[type=submit]');
  assert.equal(submitters.length, 3);
  assert.equal(submitters.every(button => button.disabled), true);
  assert.equal(page.sandbox.matbotBulkSubmit(page.form), true);
  await settle(2);
  assert.equal(submitters.every(button => button.disabled), true);
});
