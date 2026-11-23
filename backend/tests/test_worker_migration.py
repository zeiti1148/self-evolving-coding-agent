"""A failed migration must not stop the automatic worker from starting."""
from backend.experience import automation


def test_worker_starts_even_when_a_migration_raises(tmp_path, monkeypatch):
    def broken(path):
        raise RuntimeError('locked database')

    monkeypatch.setattr(automation, 'reschedule_local_comparisons', broken)
    monkeypatch.setattr('backend.experience.jobs.reschedule_relations', broken)
    # Keep the thread alive without touching the database.
    monkeypatch.setattr(automation.AutomaticMergeWorker, 'run', lambda self: self.stop_event.wait(5))
    worker = automation.AutomaticMergeWorker(tmp_path / 'worker.db', lambda: None, None, None)
    try:
        worker.start()
        assert worker.thread.is_alive()
    finally:
        worker.stop_event.set()
        worker.thread.join(timeout=3)
    assert not worker.thread.is_alive()
