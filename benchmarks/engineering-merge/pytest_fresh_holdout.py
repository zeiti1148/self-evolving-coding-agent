"""New, unseen input-shape and inheritance-order checks after suite expansion."""
import itertools
from types import SimpleNamespace
import pytest
from _pytest.mark.structures import get_unpacked_marks


def value(name, shape):
    mark = pytest.mark.xfail(name)
    return {'list': [mark], 'decorator': mark, 'mark': mark.mark}[shape]


@pytest.mark.parametrize('shapes', list(itertools.product(['list','decorator','mark'], repeat=3)))
def test_mixed_marker_representations_across_three_levels(shapes):
    class A:
        pytestmark = value('A', shapes[0])
    class B(A):
        pytestmark = value('B', shapes[1])
    class C(B):
        pytestmark = value('C', shapes[2])
    assert [m.args[0] for m in get_unpacked_marks(C)] == ['C','B','A']
    assert [m.args[0] for m in get_unpacked_marks(C, consider_mro=False)] == ['C']


@pytest.mark.parametrize('order', list(itertools.permutations('ABC')))
def test_reordering_unrelated_bases_changes_marker_order(order):
    bases={name:type(name,(),{'pytestmark':value(name,'decorator')}) for name in 'ABC'}
    child=type('CollectedTest',tuple(bases[name] for name in order),{})
    assert [m.args[0] for m in get_unpacked_marks(child)] == list(order)


@pytest.mark.parametrize('shape',['list','decorator','mark'])
def test_nonclass_objects_still_normalize_single_or_list_mark(shape):
    obj=SimpleNamespace(pytestmark=value('plain',shape))
    assert [m.args[0] for m in get_unpacked_marks(obj)] == ['plain']
