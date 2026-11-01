"""Bounded provenance traversal; missing/cyclic stored provenance is an error."""
from collections import deque

from backend.shared import ProvenanceGraph
from .database import connect, storage_error
from .promotion import get_source
from .store import _uuid, validate_integer, validation_error


def get_provenance(path, experience_id, *, max_nodes=100, max_depth=10):
    experience_id = _uuid(experience_id)
    validate_integer(max_nodes, "max_nodes", 1)
    validate_integer(max_depth, "max_depth", 0)
    if max_nodes > 100 or max_depth > 10:
        raise validation_error("Provenance is limited to 100 nodes and depth 10.")
    nodes, edges, seen, truncated = [], [], set(), False
    queue = deque([(experience_id, 0, frozenset(), None)])
    with connect(path) as connection:
        connection.execute("BEGIN")
        while queue:
            identifier, depth, ancestors, edge = queue.popleft()
            if identifier in ancestors:
                raise storage_error()
            if identifier not in seen and (len(nodes) >= max_nodes or depth > max_depth):
                truncated = True
                continue
            if edge and edge not in edges:
                edges.append(edge)
            if identifier in seen:
                continue
            item = get_source(connection, identifier)
            seen.add(identifier)
            nodes.append(item)
            for relation, targets in (("merged_from", item.merged_from), ("derived_from", item.derived_from),
                                      ("supersedes_id", [item.supersedes_id] if item.supersedes_id else [])):
                for target in targets:
                    queue.append((target, depth + 1, ancestors | {identifier},
                                  {"source": identifier, "target": target, "relation": relation}))
    # A node reached along a second branch may close a cycle that the BFS ancestry did not see.
    adjacency = {item.id: [] for item in nodes}
    for edge in edges:
        adjacency[edge["source"]].append(edge["target"])
    visiting, finished = set(), set()
    def visit(identifier):
        if identifier in visiting:
            raise storage_error()
        if identifier in finished:
            return
        visiting.add(identifier)
        for target in adjacency[identifier]:
            visit(target)
        visiting.remove(identifier)
        finished.add(identifier)
    for identifier in adjacency:
        visit(identifier)
    return ProvenanceGraph(nodes=nodes, edges=edges, truncated=truncated)
