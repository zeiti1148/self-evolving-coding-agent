from dataclasses import replace
import sqlite3

from fastapi.testclient import TestClient
import pytest

from backend.api.main import create_app
from backend.config import Settings
from backend.tests.test_merge_suggestions import record


@pytest.fixture
def application(tmp_path):
    return create_app(replace(Settings(), database_path=tmp_path / "api.db", workspace_root=tmp_path / "work"))


def test_suggestions_route_filters_without_saving_and_confirmation_remains_explicit(application):
    store = application.state.experiences
    a, b = record(store), record(store)
    with TestClient(application) as client:
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


def test_corrupt_record_returns_safe_storage_error(application):
    store = application.state.experiences
    saved = record(store)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute('UPDATE experiences SET details=? WHERE id=?', ('{private invalid data', saved.id))
    with TestClient(application) as client:
        response = client.get('/api/experiences/merge-suggestions')
        assert response.status_code == 503
        assert 'private' not in response.text and str(store.database_path) not in response.text
