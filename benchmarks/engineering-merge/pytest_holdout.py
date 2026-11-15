"""Independent invariants, never included in the model repair workspace."""
import pytest
from _pytest.mark.structures import get_unpacked_marks


def labels(cls, **kwargs):
    return [mark.args[0] for mark in get_unpacked_marks(cls, **kwargs)]


def test_diamond_root_once_and_c3_order():
    @pytest.mark.xfail('root')
    class Root:
        pass
    @pytest.mark.xfail('left')
    class Left(Root):
        pass
    @pytest.mark.xfail('right')
    class Right(Root):
        pass
    @pytest.mark.xfail('leaf')
    class Leaf(Left, Right):
        pass
    assert labels(Leaf) == ['leaf', 'left', 'right', 'root']


def test_unmarked_child_local_lookup_is_empty():
    @pytest.mark.xfail('parent')
    class Parent:
        pass
    class Child(Parent):
        pass
    assert labels(Child, consider_mro=False) == []
    assert labels(Child) == ['parent']


def test_decorating_child_never_changes_parent_or_sibling():
    @pytest.mark.xfail('parent')
    class Parent:
        pass
    class Sibling(Parent):
        pass
    @pytest.mark.xfail('child')
    class Child(Parent):
        pass
    assert labels(Parent) == ['parent']
    assert labels(Sibling) == ['parent']
    assert labels(Child) == ['child', 'parent']
    assert labels(Child, consider_mro=False) == ['child']


def test_same_mark_name_on_two_bases_is_not_deduplicated():
    @pytest.mark.xfail('condition_a')
    class A:
        pass
    @pytest.mark.xfail('condition_b')
    class B:
        pass
    class TestClass(A, B):
        pass
    assert labels(TestClass) == ['condition_a', 'condition_b']


def test_late_parent_mark_is_not_a_stale_child_copy():
    @pytest.mark.xfail('early')
    class Parent:
        pass
    @pytest.mark.xfail('child')
    class Child(Parent):
        pass
    pytest.mark.xfail('late')(Parent)
    assert labels(Child) == ['child', 'early', 'late']


def test_function_mark_order_remains_decoration_order():
    @pytest.mark.xfail('outer')
    @pytest.mark.xfail('inner')
    def function():
        pass
    assert labels(function) == ['inner', 'outer']


def test_unmarked_class_is_empty_list():
    class Plain:
        pass
    assert get_unpacked_marks(Plain) == []
