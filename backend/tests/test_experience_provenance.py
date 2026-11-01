from uuid import uuid4
import json
import sqlite3

import pytest

from backend.experience import ExperienceStore
from backend.shared import ApplicationError
from backend.tests.test_experience_promotion import draft, source, publish


def test_provenance_contains_merge_publication_and_revision_with_bounds(tmp_path):
    store = ExperienceStore(tmp_path / "provenance.db")
    left, right = source(store), source(store)
    merged = store.merge_experiences([left.id, right.id])
    first = publish(store, merged.id)
    current = publish(store, merged.id, draft(solution="Revised target conditions"), supersedes_id=first.id)
    method = getattr(store, "get_provenance", None)
    assert method is not None, "bounded provenance is required"
    graph = method(current.id)
    assert {node.id for node in graph.nodes} == {left.id, right.id, merged.id, first.id, current.id}
    assert {edge["relation"] for edge in graph.edges} == {"merged_from", "derived_from", "supersedes_id"}
    assert not graph.truncated
    limited = method(current.id, max_nodes=2)
    assert limited.truncated and len(limited.nodes) == 2
    assert method(current.id, max_depth=1).truncated
    with pytest.raises(ApplicationError):
        method(str(uuid4()))


def test_shared_nodes_cannot_hide_a_cycle_in_stored_provenance(tmp_path):
    store = ExperienceStore(tmp_path / "cycle.db")
    root, left, right = source(store), source(store), source(store)
    # root -> left and right, left -> right, right -> left. A BFS seen-set alone misses this cycle.
    with sqlite3.connect(store.database_path) as db:
        for record, targets in [(root, [left.id, right.id]), (left, [right.id]), (right, [left.id])]:
            details = json.loads(db.execute("SELECT details FROM experiences WHERE id=?", (record.id,)).fetchone()[0])
            details["derived_from"] = targets
            db.execute("UPDATE experiences SET details=? WHERE id=?", (json.dumps(details), record.id))
    with pytest.raises(ApplicationError):
        store.get_provenance(root.id)
