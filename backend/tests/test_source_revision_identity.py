"""Project revisions track verified source contents, not the host app's commits."""
import subprocess
import os
import sys
from pathlib import Path

import pytest

from backend.projects import same_source_revision, source_revision


def test_unicode_git_root_is_readable_without_python_utf8_mode(tmp_path):
    root = tmp_path / "中文工程"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "Initial"],
                   capture_output=True, check=True)
    code = ("from pathlib import Path; import sys; from backend.projects import source_revision; "
            "print(source_revision(Path(sys.argv[1]), baseline={}))")
    result = subprocess.run([sys.executable, "-X", "utf8=0", "-c", code, str(root)],
                            cwd=Path(__file__).resolve().parents[2],
                            env=os.environ | {"PYTHONUTF8": "0", "PYTHONIOENCODING": "utf-8"},
                            capture_output=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("git:") and ";tree:" in result.stdout


def test_uploaded_source_does_not_inherit_host_git_commits(tmp_path):
    host = tmp_path / "host"
    host.mkdir()
    upload = host / "runtime" / "uploaded" / "source"
    upload.mkdir(parents=True)
    (upload / "app.py").write_text("def value(): return 1\n")
    (host / "README.md").write_text("Host application\n")

    def git(*args):
        result = subprocess.run(["git", "-C", str(host), "-c", "user.name=Revision fixture",
            "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", *args],
            capture_output=True, text=True, check=True)
        return result.stdout.strip()

    git("init")
    git("add", "README.md")
    git("commit", "-m", "Initial host")
    initial = source_revision(upload)
    assert initial.startswith("tree:") and ";" not in initial
    host_before = source_revision(host)
    assert host_before.startswith("git:")
    git("commit", "--allow-empty", "-m", "Unrelated host metadata")
    assert source_revision(upload) == initial
    host_after = source_revision(host)
    assert host_after != host_before and same_source_revision(host_before, host_after)
    (upload / "app.py").write_text("def value(): return 2\n")
    assert not same_source_revision(source_revision(upload), initial)


@pytest.mark.parametrize("left,right,expected", [
    ("git:" + "a" * 40 + ";tree:" + "b" * 64, "tree:" + "b" * 64, True),
    ("git:" + "a" * 64 + ";tree:" + "b" * 64, "git:" + "c" * 40 + ";tree:" + "b" * 64, True),
    ("tree:" + "a" * 64, "tree:" + "b" * 64, False),
    ("contract-one", "contract-one", True),
    ("contract-one", "contract-two", False),
    ("git:bad;tree:" + "a" * 64, "tree:" + "a" * 64, False),
    ("git:" + "a" * 41 + ";tree:" + "b" * 64, "tree:" + "b" * 64, False),
    ("prefix;tree:" + "a" * 64, "tree:" + "a" * 64, False),
    ("tree:" + "a" * 64 + "\n", "tree:" + "a" * 64, False),
    (None, None, False),
])
def test_only_complete_revision_formats_share_source_identity(left, right, expected):
    assert same_source_revision(left, right) is expected
    assert same_source_revision(right, left) is expected


def test_legacy_git_prefixed_rule_matches_same_tree_without_weakening_named_contracts(tmp_path):
    from backend.experience import ExperienceStore
    from backend.tests.test_experience_consolidation import record, save, scan

    store = ExperienceStore(tmp_path / "rules.db")
    old = "git:" + "a" * 40 + ";tree:" + "b" * 64
    current = "tree:" + "b" * 64
    source = save(store, record(source_revision=old, compatibility_key=old))
    scan(store)
    context = dict(language="python", repository_id=source.repository_id, error_types=[], source_revision=current)
    assert [entry["id"] for entry in store.retrieve_model_references(**context)] == [source.id]
    assert store.retrieve_model_references(**context, compatibility_key=current)
    assert store.retrieve_model_references(**context, compatibility_key="different-business-contract") == []
    assert store.retrieve_model_references(**(context | {"source_revision": "tree:" + "c" * 64})) == []
    # A literal contract matching an old revision's syntax does not become
    # source-derived when the current source has a different content digest.
    assert store.retrieve_model_references(**(context | {"source_revision": "tree:" + "c" * 64}),
        compatibility_key=current) == []
    assert store.get_experience(source.id) == source


def test_automatic_maintenance_compares_legacy_revisions_without_rewriting_sources(tmp_path, monkeypatch):
    from backend.experience import ExperienceStore
    from backend.experience.database import connect
    from backend.tests.test_experience_consolidation import record, save, scan

    monkeypatch.setattr("backend.experience.jobs.PAGE_SIZE", 1)
    store = ExperienceStore(tmp_path / "maintenance.db")
    tree = "tree:" + "b" * 64
    old = "git:" + "a" * 40 + ";" + tree
    values = [
        record(source_revision=old, compatibility_key=old),
        record(source_revision=tree, compatibility_key=None),
        record(source_revision="tree:" + "c" * 64, compatibility_key=None),
        record(source_revision=tree, compatibility_key="business-contract-v2"),
        # Looks like a generated revision but belongs to a distinct source:
        # this explicit contract must not gain inferred compatibility.
        record(source_revision="tree:" + "d" * 64, compatibility_key="git:" + "e" * 40 + ";" + tree),
    ]
    saved = [save(store, value) for value in values]
    with connect(store.database_path) as db:
        ids = {row["experience_id"]: row["rule_id"] for row in db.execute("SELECT * FROM knowledge_sources")}
        before = {row["id"]: (row["fingerprint"], row["data"]) for row in db.execute("SELECT * FROM knowledge_rules")}
    scan(store)
    with connect(store.database_path) as db:
        relations = [dict(row) for row in db.execute("SELECT * FROM knowledge_relations")]
        after = {row["id"]: (row["fingerprint"], row["data"]) for row in db.execute("SELECT * FROM knowledge_rules")}
        assert all(row[0] == "succeeded" for row in db.execute("SELECT state FROM knowledge_jobs"))
    expected = {ids[saved[0].id], ids[saved[1].id]}
    assert len(relations) == 1 and {relations[0]["left_rule"], relations[0]["right_rule"]} == expected
    assert relations[0]["kind"] == "equivalent"
    assert before == after and all(store.get_experience(entry.id) == entry for entry in saved)
    found = store.retrieve_model_references(language="python", repository_id=saved[0].repository_id,
                                           error_types=[], source_revision=tree)
    assert len(found) == 1 and set(found[0]["source_ids"]) == {saved[0].id, saved[1].id}


def test_relation_classifier_needs_provenance_before_ignoring_git_metadata():
    from backend.experience.relations import classify
    tree = "tree:" + "b" * 64
    left = dict(language="python", repository_id="repo", scope="project", compatibility_key="git:" + "a" * 40 + ";" + tree,
        issue=dict(error_type="ValueError", files=["app.py"], symbols=[], root_cause="Missing check", solution="Check input"),
        applicability=["Missing input"], constraints=[], required_capabilities=[])
    right = left | {"compatibility_key": tree}
    assert classify(left, right)[0] == "different_scope"
    assert classify(left, right, verified_source_compatibility=True)[0] == "equivalent"
    assert classify(left, right | {"compatibility_key": "named-contract"}, verified_source_compatibility=True)[0] == "different_scope"
