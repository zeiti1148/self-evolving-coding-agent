"""Multi-agent retrieval honours the same representation switch as single-agent runs."""
from backend.agent.orchestrator import MultiAgentCoordinator


class NoStore:
    def retrieve_model_references(self, *args, **kwargs):
        return []


def coordinator(**options):
    return MultiAgentCoordinator(lambda: None, lambda *args, **kwargs: None, NoStore(), **options)


def test_first_injection_full_then_compact():
    agent = coordinator(compact_after_first=True)
    assert [agent.representation() for _ in range(3)] == ['full', 'compact', 'compact']


def test_disabled_by_default_changes_nothing():
    agent = coordinator()
    assert [agent.representation() for _ in range(3)] == ['full', 'full', 'full']
