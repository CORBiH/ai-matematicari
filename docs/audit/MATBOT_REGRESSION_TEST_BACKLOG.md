# MatBot — Regression test backlog (iz audita 2026-07-11)

Poredano po vrijednosti (bug-severity × vjerovatnoća regresije). Svi testovi su
mockirani (bez API), osim gdje piše LIVE. Konvencije prate postojeći suite
(`tests/`, fixtures `master`/`tmap`/`fake_chat`).

## P0 — uz Fazu A fixeve (crveni danas, zeleni poslije fixa)

1. **test_practice_fresh_multi_image_uses_image_tasks** (AUD-01)
   `tests/test_image_test_state.py` — fresh `image_ocr_text` ≥2 stavke +
   `mode=practice` + prazna poruka → `payload["_image_test"]` aktivan I prompt
   sadrži doslovne OCR zadatke; odgovor na stavku 1 gradi se protiv OCR stavke.

2. **test_ordinal_named_answers_parsed** (AUD-02)
   `tests/test_answer_checker*.py` — `parse_student_answers("prvi je 6/9,
   drugi 4/8, treci ne znam")` → numbered {1:6/9, 2:4/8}, stavka 3 nepokušana;
   protiv-primjer: „prvi korak je da nađeš nazivnik" → NE parsira kao odgovor.

3. **test_ordinal_multi_updates_task_items** (AUD-02, service nivo)
   `tests/test_bug_batch_*.py` — service poziv sa seed multi-taskom →
   `next_state.task_items.graded == [1, 2]`.

4. **test_challenge_message_not_graded_against_active_task** (AUD-06)
   parametrizovano: „pogrijesio si, nastavnica kaze da je 2/5" /
   „nije tacno, meni je ispalo 2/8 i sigurno je tako" → `_skip_answer_check`,
   bez ocjenske labele; kontra-slučaj „nije tacno, sad mislim 5/6" → gradi se.

5. **test_new_task_marker_ignored_while_items_pending** (AUD-04)
   grading potez, `task_items.graded=[2]`, model vrati „Zadatak: …" marker →
   `last_tutor_task` ostaje multi zadatak, `task_items` sačuvan.

## P1

6. **test_derive_expected_percent_of** (AUD-05) — „35% od 40"→14,
   „Koliko je 25% od 80?"→20, „20% nekog broja je 15"→75.
7. **test_derive_expected_parenthesized_negatives** (AUD-05) — „(-3) · 4"→−12,
   „(2 + 3) · 4" ostaje 20 (već radi — čuvar).
8. **test_derive_expected_powers_and_units** (AUD-05) — 2^6→64;
   „cm u 3,5 m"→350.
9. **test_label_contract_without_checker** (AUD-11) — kada `answer_check`
   nema presudu, guard/prompt i dalje traže label-first (nakon A3 postaje moot
   za pokrivene forme; čuvar za nepokrivene).
10. **test_classifier_eval_list** (AUD-03, LIVE-eval, van pytest) —
    `docs/eval` lista 30 poruka→topic-prefix, prag 80%; sedmični smoke.

## P2

11. **test_quick_multi_image_asks_deterministically** (AUD-07) — multi OCR +
    prazna poruka u quick → ask-message bez ijednog rezultata (poslije B3).
12. **test_continuation_block_forbids_task_marker** (AUD-08) —
    `build_continuation_instructions` sadrži anti-„Zadatak:" pravilo.
13. **test_multi_item_no_leading_summary_label** (AUD-10) — guard skida vodeću
    samostalnu labelu na multi-item potezu; „1. 1." → „1.".
14. **test_sse_done_state_parity** — mockirano: `handle_chat` vs
    `handle_chat_stream` done payload jednaki u mode/session_mode/status/
    last_tutor_task-prisustvo/next_state (audit H1, offline verzija).
15. **test_bosnian_hr_additions** (AUD-09) — okomit/zbroj/decimalna tačka/Pithagor.

## P3

16. **test_conversation_flow_ladder** — 4 uzastopna tačna kroz Client simulator:
    streak raste, svaki put novi zadatak, bez ponavljanja istog zadatka.
17. **check_js.mjs proširenja** — payload uslovi (previous_next_state uvijek;
    last_tutor_task samo answer-faza; last_image_context na image follow-up).
18. **test_repetition_guard_eval** — offline eval metrika sličnosti uzastopnih
    pohvala < 0.8 (poslije D3).

## Napomene o postojećem suite-u
- Nisu nađeni duplikati vrijedni brisanja; suite je single-turn orijentisan —
  najveća vrijednost je E1 (multi-turn fixtures sa state-carry simulatorom).
- `audit_lib.py` Client (scratchpad) je spreman kostur za E1 — prenijeti u
  `tests/helpers/` bez live_chat dijela.
