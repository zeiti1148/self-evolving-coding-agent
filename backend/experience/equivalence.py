"""Complete, directly verified equivalence groups with identical prerequisites."""


def equivalent_groups(identities, relations, dependencies):
    groups = {identity: {identity} for identity in identities}
    for (left, right), kind in relations.items():
        if kind == 'equivalent' and left in groups and right in groups:
            combined = groups[left] | groups[right]
            for identity in combined:
                groups[identity] = combined
    result = {}
    for identity, group in groups.items():
        complete = all(a == b or relations.get(tuple(sorted((a, b)))) == 'equivalent'
                       for a in group for b in group)
        same_dependencies = all(dependencies.get(member, set()) == dependencies.get(identity, set())
                                for member in group)
        result[identity] = group if complete and same_dependencies else {identity}
    return result
