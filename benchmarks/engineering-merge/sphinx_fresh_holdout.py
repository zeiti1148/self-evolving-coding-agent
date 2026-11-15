"""Unseen end-to-end checks for the documented inherited-members cutoff."""
import sys
import pytest
from sphinx.testing.path import path as SphinxPath
from sphinx.testing.util import SphinxTestApp


@pytest.fixture(scope='module')
def pages(tmp_path_factory):
    directory=tmp_path_factory.mktemp('sphinx-cutoff')
    (directory/'cutoff_base.py').write_text('''class Root:
    def __init__(self):
        self.root_value = None  #: ROOT_CUTOFF_VALUE

class Middle(Root):
    def __init__(self):
        self.middle_value = None  #: MIDDLE_RETAIN_VALUE
''',encoding='utf-8')
    (directory/'cutoff_leaf.py').write_text('''from cutoff_base import Middle
class Leaf(Middle):
    def __init__(self):
        self.leaf_value = None  #: LEAF_RETAIN_VALUE
''',encoding='utf-8')
    (directory/'conf.py').write_text("import sys\nsys.path.insert(0,"+ascii(str(directory))+")\nextensions=['sphinx.ext.autodoc']\nmaster_doc='index'\n",encoding='utf-8')
    for name,option in [('all',''),('cutoff','Root')]:
        (directory/(name+'.rst')).write_text(name+'\n========\n\n.. autoclass:: cutoff_leaf.Leaf\n   :members:\n   :inherited-members: '+option+'\n',encoding='utf-8')
    (directory/'index.rst').write_text('Index\n=====\n\n.. toctree::\n\n   all\n   cutoff\n',encoding='utf-8')
    app=SphinxTestApp(srcdir=SphinxPath(str(directory)),buildername='html',freshenv=True)
    try:
        app.build(force_all=True)
        assert 'failed to import' not in app._warning.getvalue()
        yield {name:(directory/'_build/html'/(name+'.html')).read_text(encoding='utf-8') for name in ['all','cutoff']}
    finally:
        app.cleanup()
        for name in ['cutoff_base','cutoff_leaf']:
            sys.modules.pop(name,None)


@pytest.mark.parametrize('token',['ROOT_CUTOFF_VALUE','MIDDLE_RETAIN_VALUE','LEAF_RETAIN_VALUE'])
def test_default_includes_every_declared_level(pages,token):
    assert token in pages['all']


def test_explicit_cutoff_hides_root_declarations(pages):
    assert 'ROOT_CUTOFF_VALUE' not in pages['cutoff']


@pytest.mark.parametrize('token',['MIDDLE_RETAIN_VALUE','LEAF_RETAIN_VALUE'])
def test_explicit_cutoff_keeps_declarations_above_root(pages,token):
    assert token in pages['cutoff']
