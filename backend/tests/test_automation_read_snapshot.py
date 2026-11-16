from pathlib import Path

from backend.experience import recovery
from backend.experience.automation import feed
from backend.experience.database import connect
from backend.experience.library import library
from backend.tests.test_experience_recovery import prepared


def test_feed_checks_each_grant_once_and_rechecks_changed_files_next_request(tmp_path, monkeypatch):
    store, sources, verifier, manager, service = prepared(tmp_path)
    card = next(c for c in library(store.database_path)['items'] if c['state'] == 'paused')
    recovery.enqueue(store.database_path, card['id'])
    assert service.run_once()
    with connect(store.database_path) as connection:
        grant = recovery.active_grants(connection)[card['id']]

    checks = []
    original = recovery.grant_files_valid

    def checked(value):
        valid = original(value)
        checks.append((value['rule_id'], valid))
        return valid

    def nested_connection(*args, **kwargs):
        raise AssertionError('A feed must reuse its database snapshot for sources and history')

    monkeypatch.setattr(recovery, 'grant_files_valid', checked)
    monkeypatch.setattr('backend.experience.knowledge.connect', nested_connection)
    monkeypatch.setattr('backend.experience.reviews.connect', nested_connection)
    first = feed(store.database_path)
    assert len(first['items']) == 2
    assert any(c.get('history') for job in first['items'] for c in job['changes'])
    assert checks == [(card['id'], True)]
    checks.clear()
    assert feed(store.database_path) == first
    assert checks == [(card['id'], True)]
    Path(grant['artifacts'][0]['path']).write_text('changed test evidence')
    checks.clear()
    feed(store.database_path)
    assert checks == [(card['id'], False)]


def test_empty_feed_does_not_scan_unrelated_evidence(tmp_path, monkeypatch):
    from backend.experience import ExperienceStore
    store = ExperienceStore(tmp_path / 'empty.db')
    store.initialize()
    def unexpected(*args, **kwargs):
        raise AssertionError('No visible jobs require no evidence verification')
    monkeypatch.setattr(recovery, 'active_grants', unexpected)
    assert feed(store.database_path) == {'items': [], 'next_offset': None}
