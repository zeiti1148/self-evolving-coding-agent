"""The budget measurement drives production retrieval, so pin its contract."""
from backend.experience import ExperienceStore
from backend.experience.jobs import drain
from scripts.measure_injection_budget import measure, synthetic


def test_measure_reports_the_strategy_budget_and_eligible_rules(tmp_path):
    store = ExperienceStore(tmp_path / 'budget.db')
    spec = synthetic(store, count=1, padding=60, repository_id='benchmark/measure')
    from scripts.measure_injection_budget import build
    source = build(store, spec)
    drain(store.database_path, seconds=20)
    contextual = measure(store.database_path, source, 'contextual')
    adaptive = measure(store.database_path, source, 'adaptive')
    # Both strategies report their own cap from the shared table.
    assert (contextual['limit'], contextual['character_budget']) == (5, 16000)
    assert (adaptive['limit'], adaptive['character_budget']) == (3, 8000)
    # The eligible rule reaches the payload under both budgets.
    assert contextual['rules'] == 1 and adaptive['rules'] == 1
    assert contextual['characters'] > 0 and contextual['characters'] == adaptive['characters']


def test_measure_never_exceeds_the_character_budget(tmp_path):
    store = ExperienceStore(tmp_path / 'long.db')
    spec = synthetic(store, count=3, padding=9000, repository_id='benchmark/long')
    from scripts.measure_injection_budget import build
    source = build(store, spec)
    drain(store.database_path, seconds=20)
    adaptive = measure(store.database_path, source, 'adaptive')
    assert adaptive['characters'] <= 8000
