"""Prompt-strengthening pass (produkcijski nalaz: dva uzastopna odbijena
generisanja zbog semantically_duplicate_options, request_id=e4eb30672481 i
3b9133c47a62, oba na lekciji "Proširivanje razlomaka", 6-04-005). Server-side
validator je ispravno odbio oba — cilj ovog prolaza je SMANJITI učestalost
odbijanja jačanjem prompta, ne slabljenjem validatora. Kategorije 1-5."""
from matbot import prompts, task_family_validation as tfv


def _build_options_instructions():
    return prompts.build_instructions(6, lesson_title="Proširivanje razlomaka", oblast="Razlomci")


def test_case1_prompt_requires_all_six_pairwise_comparisons():
    text = _build_options_instructions()
    assert "ŠEST parova" in text
    assert "1-2, 1-3, 1-4, 2-3, 2-4, 3-4" in text


def test_case2_prompt_forbids_equivalent_fractions():
    text = _build_options_instructions()
    assert "\\frac{5}{12}" in text and "\\frac{15}{36}" in text
    assert "\\frac{2}{3}" in text and "\\frac{8}{12}" in text


def test_case3_prompt_forbids_exact_vs_rounded_pair():
    text = _build_options_instructions()
    assert "8\\sqrt{2}\\,\\text{cm}" in text
    assert "11,3\\,\\text{cm}" in text


def test_case4_prompt_forbids_reordered_symbolic_expressions():
    text = _build_options_instructions()
    assert "a\\sqrt{2}" in text and "\\sqrt{2}a" in text
    assert "2a" in text and "a\\cdot2" in text
    # dodatni primjer iz ovog zahtjeva: sređen vs nesređen isti izraz
    assert "24\\sqrt{3}" in text and "\\frac{48\\sqrt{3}}{2}" in text


def test_case5_expand_to_given_denominator_requires_single_equivalent_fraction():
    block = tfv.prompt_block("expand_to_given_denominator")
    assert "JEDINSTVENOST OPCIJA" in block
    assert "tačno JEDNA opcija" in block.lower() or "JEDNA opcija" in block
    assert "$\\frac{15}{36}$" in block  # konkretan primjer iz zahtjeva
    assert "$\\frac{5}{12}$" in block


def test_case17_expand_note_states_every_required_requirement_after_trimming():
    """Nakon skraćivanja (Phase 4) napomena i dalje MORA nositi svih šest
    obaveznih tvrdnji — kraće, ali ne slabije."""
    note = tfv.contract_for("expand_to_given_denominator").prompt_option_uniqueness_note
    assert "skrati" in note and "unakrsno pomnoži" in note      # skrati/unakrsno pomnoži sve opcije
    assert "Tačno JEDNA opcija" in note                          # tačno jedna jednaka originalu
    assert "traženi nazivnik" in note                            # jedina s traženim nazivnikom
    assert "Originalni razlomak NIKAD nije opcija" in note       # original nije opcija
    assert "nijedne dvije opcije ne smiju se skratiti na isti razlomak" in note
    assert "svih šest parova" in note                            # poređenje svih šest parova


def test_case18_family_note_does_not_duplicate_global_prompt_verbatim():
    """Napomena porodice smije DOPUNITI globalna pravila, ali ne smije doslovno
    ponavljati njihove duge rečenice (nepotrebno troši izlazni budžet)."""
    note = tfv.contract_for("expand_to_given_denominator").prompt_option_uniqueness_note
    global_text = _build_options_instructions()
    # nijedna dovoljno duga rečenica iz napomene ne smije se doslovno naći u globalnom promptu
    for sentence in [s.strip() for s in note.split(".") if len(s.strip()) > 40]:
        assert sentence not in global_text, f"duplicated verbatim: {sentence[:60]}"
    # i napomena mora ostati sažeta (živi nalaz: prvobitnih 842 znaka je skraćeno)
    assert len(note) < 650


def test_family_specific_uniqueness_notes_present_for_all_six_fraction_families():
    families = (
        "expand_to_given_denominator", "find_expansion_factor", "find_missing_numerator",
        "recognize_equivalent_fraction", "compare_fractions", "fraction_operation",
    )
    for family in families:
        contract = tfv.contract_for(family)
        assert contract is not None, family
        assert contract.prompt_option_uniqueness_note, f"missing uniqueness note for {family}"
        assert contract.prompt_option_uniqueness_note in tfv.prompt_block(family)
