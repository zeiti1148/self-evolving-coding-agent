"""Exercise real Sphinx HTML builds; these tests were not given to the repairer.

The upstream regression is one superclass in one module. These add a diamond
across modules, inherited-member opt-out, overrides, and name collisions.
"""
from pathlib import Path
import re
import sys

import pytest
from sphinx.testing.path import path as SphinxPath
from sphinx.testing.util import SphinxTestApp

BASE = '''class Root:
    def __init__(self):
        self.root_token = None  #: ROOT_UNIQUE_DOC
        self.shared = None  #: ROOT_SHARED_DOC

class Left(Root):
    def __init__(self):
        self.left_token = None  #: LEFT_UNIQUE_DOC
        self.shared = None  #: LEFT_SHARED_DOC

class Right(Root):
    def __init__(self):
        self.right_token = None  #: RIGHT_UNIQUE_DOC
        self.shared = None  #: RIGHT_SHARED_DOC
'''
DERIVED = '''from upstream_bases import Left, Right

class Leaf(Left, Right):
    def __init__(self):
        self.leaf_token = None  #: LEAF_UNIQUE_DOC

class Override(Left, Right):
    def __init__(self):
        self.shared = None  #: CHILD_OVERRIDE_DOC

class Reverse(Right, Left):
    pass
'''


@pytest.fixture(scope='module')
def built(tmp_path_factory):
    directory = tmp_path_factory.mktemp('real-sphinx-mro')
    (directory / 'upstream_bases.py').write_text(BASE, encoding='utf-8')
    (directory / 'upstream_derived.py').write_text(DERIVED, encoding='utf-8')
    (directory / 'conf.py').write_text(
        "import sys\nsys.path.insert(0, " + ascii(str(directory)) + ")\nextensions = ['sphinx.ext.autodoc']\nmaster_doc = 'index'\n",
        encoding='utf-8')
    pages = {
        'inherited': ('Leaf', True), 'local': ('Leaf', False),
        'override': ('Override', True), 'reverse': ('Reverse', True),
    }
    for name, (cls, inherited) in pages.items():
        rst = name + '\n' + '=' * len(name) + '\n\n.. autoclass:: upstream_derived.' + cls + '\n   :members:\n'
        if inherited:
            rst += '   :inherited-members:\n'
        (directory / (name + '.rst')).write_text(rst, encoding='utf-8')
    (directory / 'index.rst').write_text('Index\n=====\n\n.. toctree::\n\n   ' + '\n   '.join(pages) + '\n',encoding='utf-8')
    app = SphinxTestApp(srcdir=SphinxPath(str(directory)), buildername='html', freshenv=True)
    try:
        app.build(force_all=True)
        result = {name: (directory / '_build/html' / (name + '.html')).read_text(encoding='utf-8') for name in pages}
        assert 'failed to import' not in app._warning.getvalue(), app._warning.getvalue()
        yield result
    finally:
        app.cleanup()
        for name in ['upstream_bases', 'upstream_derived']:
            sys.modules.pop(name, None)


@pytest.mark.parametrize('token', ['ROOT_UNIQUE_DOC', 'LEFT_UNIQUE_DOC', 'RIGHT_UNIQUE_DOC', 'LEAF_UNIQUE_DOC'])
def test_all_diamond_members_from_different_modules(built, token):
    assert token in built['inherited']


def test_common_ancestor_member_is_emitted_once(built):
    assert built['inherited'].count('ROOT_UNIQUE_DOC') == 1


def test_mro_collision_chooses_left_parent(built):
    assert 'LEFT_SHARED_DOC' in built['inherited']
    assert 'RIGHT_SHARED_DOC' not in built['inherited']
    assert 'ROOT_SHARED_DOC' not in built['inherited']


def test_reversed_bases_choose_the_new_first_parent(built):
    assert 'RIGHT_SHARED_DOC' in built['reverse']
    assert 'LEFT_SHARED_DOC' not in built['reverse']


def test_local_attribute_overrides_both_bases(built):
    assert 'CHILD_OVERRIDE_DOC' in built['override']
    assert 'LEFT_SHARED_DOC' not in built['override']
    assert 'RIGHT_SHARED_DOC' not in built['override']


def test_inheritance_is_opt_in(built):
    assert 'LEAF_UNIQUE_DOC' in built['local']
    for token in ['ROOT_UNIQUE_DOC', 'LEFT_UNIQUE_DOC', 'RIGHT_UNIQUE_DOC']:
        assert token not in built['local']
