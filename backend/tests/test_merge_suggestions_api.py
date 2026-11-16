from dataclasses import replace
import sqlite3
import time
from threading import Event, current_thread

from fastapi.testclient import TestClient
import pytest

from backend.api.main import create_app
from backend.config import Settings
from backend.experience.database import read_only
from backend.experience.relations import RELATION_POLICY_VERSION
from backend.tests.test_merge_suggestions import record


@pytest.fixture
def application(tmp_path):
    return create_app(replace(Settings(), database_path=tmp_path / "api.db", workspace_root=tmp_path / "work"))


def wait_for_maintenance(client, *sources):
    # Application startup may queue a policy-version refresh after fixture review.
    # Wait for that real worker instead of racing the first read-only discovery.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with read_only(client.app.state.experiences.database_path) as connection:
            policy = connection.execute("SELECT value FROM knowledge_meta WHERE key='semantic_version'").fetchone()
        if policy is None or policy[0] != RELATION_POLICY_VERSION:
            time.sleep(.01)
            continue
        statuses = [client.get(f'/api/experiences/{source.id}/maintenance').json() for source in sources]
        if all(status['rules'] and all(rule['state'] == 'ready' and rule['effective_status'] == 'active'
                                      for rule in status['rules']) for status in statuses):
            return
        time.sleep(.01)
    pytest.fail('Normal maintenance did not make fixture sources ready')


def test_suggestions_route_filters_without_saving_and_confirmation_remains_explicit(application):
    store = application.state.experiences
    a, b = record(store), record(store)
    with TestClient(application) as client:
        wait_for_maintenance(client, a, b)
        response = client.get('/api/experiences/merge-suggestions', params={'language': 'python'})
        assert response.status_code == 200
        pair, = response.json()['suggestions']
        assert pair['source_ids'] == sorted([a.id, b.id])
        assert len(store.list_experiences()) == 2
        assert client.get('/api/experiences/merge-suggestions', params={'repository_id': 'other'}).json()['suggestions'] == []
        body = {'experience_ids': pair['source_ids']}
        assert client.post('/api/experiences/merge-preview', json=body).json()['can_merge']
        assert len(store.list_experiences()) == 2
        merged = client.post('/api/experiences/merge', json=body)
        assert merged.status_code == 200
        assert client.post('/api/experiences/merge', json=body).json()['id'] == merged.json()['id']
        assert len(store.list_experiences()) == 3
        assert client.get('/api/experiences/merge-suggestions').json()['suggestions'] == []


def test_overlapping_manual_merge_invalidates_suggestion(application):
    store = application.state.experiences
    a, b, c = record(store), record(store), record(store)
    with TestClient(application) as client:
        wait_for_maintenance(client, a, b, c)
        assert client.get('/api/experiences/merge-suggestions').status_code == 200
        assert client.post('/api/experiences/merge', json={'experience_ids': [a.id, c.id]}).status_code == 200
        preview = client.post('/api/experiences/merge-preview', json={'experience_ids': [a.id, b.id]})
        assert preview.status_code == 200 and not preview.json()['can_merge']
        assert client.post('/api/experiences/merge', json={'experience_ids': [a.id, b.id]}).status_code == 409
        assert len(store.list_experiences()) == 4


@pytest.mark.parametrize('params', [{'limit': 21}, {'limit': 0}, {'language': 'ruby'}, {'scope': 'unknown'}, {'reuse_status': 'unknown'}])
def test_suggestion_query_validation(application, params):
    with TestClient(application) as client:
        assert client.get('/api/experiences/merge-suggestions', params=params).status_code == 422


@pytest.mark.parametrize('language', ['go', 'cpp'])
def test_uploaded_language_suggestions_keep_validated_preview_response(application, language):
    store = application.state.experiences
    a, b = record(store, schema_version=4, language=language), record(store, schema_version=4, language=language)
    with TestClient(application) as client:
        wait_for_maintenance(client, a, b)
        response = client.get('/api/experiences/merge-suggestions', params={'language': language})
        assert response.status_code == 200
        [pair] = response.json()['suggestions']
        assert pair['language'] == language and pair['source_ids'] == sorted([a.id, b.id])
        preview = client.post('/api/experiences/merge-preview', json={'experience_ids': pair['source_ids']})
        assert preview.status_code == 200 and preview.json()['can_merge'], preview.json()
        assert preview.json()['conflict_details'] == []


@pytest.mark.parametrize('language', ['go', 'cpp'])
def test_policy_refresh_rechecks_suggested_sources_before_merging(application, monkeypatch, language):
    from backend.experience import jobs, semantic
    store = application.state.experiences
    a, b = record(store, schema_version=4, language=language), record(store, schema_version=4, language=language)
    allow_refresh, policy_applied, allow_review = Event(), Event(), Event()
    original_claim = jobs.claim

    class DelayedSemantics:
        available = False

        def refresh(self):
            assert allow_refresh.wait(5), 'Startup policy refresh was not released'

    def pause_before_review(*args, **kwargs):
        if current_thread().name == 'experience-maintenance':
            policy_applied.set()
            assert allow_review.wait(5), 'Rule review was not released'
        return original_claim(*args, **kwargs)

    monkeypatch.setattr(semantic, 'runtime', lambda: DelayedSemantics())
    monkeypatch.setattr(jobs, 'claim', pause_before_review)
    with TestClient(application) as client:
        try:
            [pair] = client.get('/api/experiences/merge-suggestions', params={'language': language}).json()['suggestions']
            assert pair['source_ids'] == sorted([a.id, b.id])
            allow_refresh.set()
            assert policy_applied.wait(5), 'Startup policy refresh did not complete'
            body = {'experience_ids': pair['source_ids']}
            preview = client.post('/api/experiences/merge-preview', json=body)
            assert preview.status_code == 200 and not preview.json()['can_merge']
            assert {detail['code'] for detail in preview.json()['conflict_details']} == {'source_unavailable'}
            assert client.post('/api/experiences/merge', json=body).status_code == 409
            assert len(store.list_experiences()) == 2
            assert client.get('/api/experiences/merge-suggestions', params={'language': language}).json()['suggestions'] == []
            allow_review.set()
            wait_for_maintenance(client, a, b)
            assert client.post('/api/experiences/merge-preview', json=body).json()['can_merge']
        finally:
            allow_refresh.set()
            allow_review.set()


def test_corrupt_record_returns_safe_storage_error(application):
    store = application.state.experiences
    saved = record(store)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute('UPDATE experiences SET details=? WHERE id=?', ('{private invalid data', saved.id))
    with TestClient(application) as client:
        response = client.get('/api/experiences/merge-suggestions')
        assert response.status_code == 503
        assert 'private' not in response.text and str(store.database_path) not in response.text
