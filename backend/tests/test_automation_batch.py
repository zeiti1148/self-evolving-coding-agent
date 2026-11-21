"""Bounded batches: every eligible candidate is compared until the pair budget is reached."""
from backend.experience import ExperienceStore
from backend.experience.automation import AutomaticMergeWorker, feed
from backend.experience.database import connect
from backend.tests.test_automatic_repair_merge import WorkflowModel
from backend.tests.test_experience_consolidation import record, save, scan


def earlier(store, count, repository=lambda index: f'old-{index}'):
    """Save prior repairs and retire their own comparison jobs."""
    for index in range(count):
        save(store, record(repository_id=repository(index)))
    with connect(store.database_path) as connection:
        connection.execute("UPDATE experience_auto_jobs SET state='succeeded'")


class Rejecting(WorkflowModel):
    reject = True


def drain(store):
    WorkflowModel.comparisons = 0
    worker = AutomaticMergeWorker(store.database_path, Rejecting)
    while worker.run_once():
        pass
    return worker


def test_candidates_beyond_one_batch_are_still_compared(tmp_path):
    store = ExperienceStore(tmp_path/'batch.db')
    earlier(store, 12)
    current = save(store, record(repository_id='new')); scan(store)
    drain(store)
    job = feed(store.database_path, experience_id=current.id)['items'][0]
    assert job['candidate_total'] == 12
    assert job['selected_candidates'] == 12
    assert job['compared_candidates'] == 12
    assert job['deferred_candidates'] == 0
    # Disclosure adds up: every disclosed candidate is either compared or deferred.
    assert job['candidate_total'] == job['compared_candidates'] + job['deferred_candidates']
    assert WorkflowModel.comparisons == 12


def test_pair_budget_stops_and_discloses_the_remainder(tmp_path, monkeypatch):
    import backend.experience.automation as automation
    monkeypatch.setattr(automation, 'MAX_PAIRS_PER_JOB', 3)
    store = ExperienceStore(tmp_path/'budget.db')
    earlier(store, 7)
    current = save(store, record(repository_id='new')); scan(store)
    drain(store)
    job = feed(store.database_path, experience_id=current.id)['items'][0]
    assert job['state'] == 'succeeded' and '上限' in job['notice']
    assert job['compared_candidates'] == 3 and job['deferred_candidates'] == 4
    assert job['candidate_total'] == job['compared_candidates'] + job['deferred_candidates']
    assert WorkflowModel.comparisons == 3


def test_failed_pairs_count_as_compared_not_deferred(tmp_path):
    store = ExperienceStore(tmp_path/'failed.db')
    earlier(store, 6)
    current = save(store, record(repository_id='new')); scan(store)

    class Failing(WorkflowModel):
        fail = True

    WorkflowModel.comparisons = 0
    worker = AutomaticMergeWorker(store.database_path, Failing)
    while worker.run_once():
        pass
    job = feed(store.database_path, experience_id=current.id)['items'][0]
    assert job['state'] == 'failed'
    # An attempted but failed comparison is not an unexamined candidate.
    assert job['compared_candidates'] == 6 and job['deferred_candidates'] == 0
    assert job['candidate_total'] == job['compared_candidates'] + job['deferred_candidates']
    assert WorkflowModel.comparisons == 6

