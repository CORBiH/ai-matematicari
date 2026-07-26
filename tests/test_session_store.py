"""Testovi za matbot/session_store.py."""
import threading

from matbot.session_store import SessionStore


def _load(store, session_id="s1", grade=6, lesson="6-01-001", title="Unija skupova", oblast="Skupovi"):
    return store.load(session_id=session_id, grade=grade, lesson_id=lesson,
                      lesson_title=title, oblast=oblast, mode="practice")


def test_create_and_read_session():
    store = SessionStore()
    s = _load(store)
    assert s["current_task"] == ""
    s["current_task"] = "Zadatak A"
    store.save(s)
    again = _load(store)
    assert again["current_task"] == "Zadatak A"


def test_update_session():
    store = SessionStore()
    s = _load(store)
    s["hint_level"] = 2
    s["expected_answer_summary"] = "5/8"
    store.save(s)
    assert _load(store)["hint_level"] == 2
    assert _load(store)["expected_answer_summary"] == "5/8"


def test_lesson_change_resets_task():
    store = SessionStore()
    s = _load(store)
    s["current_task"] = "Zadatak A"
    s["hint_level"] = 3
    store.save(s)
    other = _load(store, lesson="6-01-002", title="Presjek skupova")
    assert other["current_task"] == ""
    assert other["hint_level"] == 0


def test_grade_and_mode_change_reset_task():
    store = SessionStore()
    s = _load(store)
    s["current_task"] = "Zadatak A"
    store.save(s)
    assert _load(store, grade=7)["current_task"] == ""


def test_recent_tasks_capped_at_3():
    store = SessionStore()
    s = _load(store)
    s["recent_tasks"] = ["t1", "t2", "t3", "t4", "t5"]
    store.save(s)
    assert _load(store)["recent_tasks"] == ["t3", "t4", "t5"]


def test_recent_turns_capped_at_3():
    store = SessionStore()
    s = _load(store)
    s["recent_turns"] = [{"student": str(i), "tutor": "x"} for i in range(6)]
    store.save(s)
    assert [t["student"] for t in _load(store)["recent_turns"]] == ["3", "4", "5"]


def test_load_returns_copy_not_reference():
    store = SessionStore()
    s = _load(store)
    s["current_task"] = "Zadatak A"
    store.save(s)
    copy1 = _load(store)
    copy1["current_task"] = "IZMIJENJENO"
    assert _load(store)["current_task"] == "Zadatak A"


def test_thread_safe_concurrent_saves():
    store = SessionStore()
    errors = []

    def worker(i):
        try:
            for _ in range(50):
                s = store.load(session_id=f"sess-{i % 5}", grade=6, lesson_id="L",
                               lesson_title="T", oblast="O", mode="practice")
                s["recent_tasks"].append(f"t{i}")
                store.save(s)
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    for i in range(5):
        s = store.peek(f"sess-{i}")
        assert s is not None
        assert len(s["recent_tasks"]) <= 3
