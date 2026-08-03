"""Univerzalni Lesson Contract Engine za Practice mod.

Paket je NAMJERNO bez ijednog ID-ja lekcije i bez ijednog naziva lekcije u
kodu. Sve što razlikuje jednu lekciju od druge živi u
`data/contract_templates.json` + `data/lesson_contracts.json`.

Ulazne tačke:
    registry.contract_for(topic_id)   → LessonContract | None
    registry.practice_state(contract) → "engine" | "legacy" | "unavailable"
    pipeline.build_plan(...)          → GenerationPlan (arhetip + izvor odluke)
    pipeline.prepare_task(...)        → PreparedTask (verifikovan serverski kostur)
    pipeline.verify_prose_fidelity(…) → kapija modelove proze uz zadatak
"""
