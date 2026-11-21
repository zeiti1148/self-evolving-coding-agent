"""Cross-verification splicing: Python keeps AST behaviour, other languages use tree-sitter."""
import pytest

from backend.experience import transplant
from backend.experience.automatic_resolution import VerificationUnavailable

PYTHON = "def greet(name):\n    return 'hi ' + name\n"
PYTHON_FIX = "def greet(name):\n    return 'hello ' + name\n"

JAVA = """package demo;
class Greeting {
    String greet(String name) {
        return "hi " + name;
    }
}
"""
JAVA_FIX = """package demo;
class Greeting {
    String greet(String name) {
        if (name == null) { return "hello, guest"; }
        return "hello " + name;
    }
}
"""

GO = "package counter\n\nfunc Count(x int) int {\n\treturn x\n}\n"
GO_FIX = "package counter\n\nfunc Count(x int) int {\n\tif x < 0 {\n\t\treturn 0\n\t}\n\treturn x\n}\n"
GO_METHOD = """package counter

type Counter struct{}

func (c Counter) Add(x int) int {
\treturn x
}
"""

CPP = """#include <string>

class Greeting {
public:
    std::string greet(const std::string& name) {
        return "hi " + name;
    }
};
"""
CPP_FIX = """#include <string>

class Greeting {
public:
    std::string greet(const std::string& name) {
        if (name.empty()) { return "hello, guest"; }
        return "hello " + name;
    }
};
"""


def test_python_transplant_replaces_only_the_body():
    output = transplant.transplant(PYTHON, PYTHON_FIX, 'python', 'greet')
    assert output.splitlines()[0] == 'def greet(name):'
    assert 'hello' in output and "'hi '" not in output


def test_python_signature_mismatch_is_rejected():
    with pytest.raises(VerificationUnavailable):
        transplant.transplant(PYTHON, "def greet(name, style):\n    return 'x'\n", 'python', 'greet')


def test_unsupported_language_and_path_are_rejected():
    with pytest.raises(VerificationUnavailable):
        transplant.transplant('x', 'y', 'ruby', 'f')
    assert transplant.supports('java') and not transplant.supports('ruby')
    assert transplant.accepts_path('java', 'A.java') and not transplant.accepts_path('java', 'a.py')
    assert transplant.accepts_path('cpp', 'a.cc') and transplant.accepts_path('python', 'a.py')


def test_changed_detects_edits_and_ignores_identical_targets():
    assert transplant.changed(PYTHON, PYTHON_FIX, 'python', 'greet')
    assert not transplant.changed(PYTHON, PYTHON, 'python', 'greet')


@pytest.mark.parametrize('language,before,after,qualified,marker,declaration', [
    ('java', JAVA, JAVA_FIX, 'Greeting.greet', 'if (name == null)', 'String greet(String name) {'),
    ('go', GO, GO_FIX, 'Count', 'if x < 0', 'func Count(x int) int {'),
    ('cpp', CPP, CPP_FIX, 'Greeting.greet', 'name.empty()', 'std::string greet(const std::string& name) {'),
])
def test_tree_sitter_transplant_keeps_the_signature(language, before, after, qualified, marker, declaration):
    pytest.importorskip('tree_sitter')
    output = transplant.transplant(before, after, language, qualified)
    assert marker in output
    # The target declaration stays: only the body comes from the donor.
    assert declaration in output
    assert 'hi ' + ('" + name' if language != 'go' else '') not in output


def test_identical_tree_sitter_bodies_are_rejected():
    pytest.importorskip('tree_sitter')
    with pytest.raises(VerificationUnavailable):
        transplant.transplant(JAVA, JAVA, 'java', 'Greeting.greet')


def test_java_missing_target_is_rejected():
    pytest.importorskip('tree_sitter')
    with pytest.raises(VerificationUnavailable):
        transplant.transplant(JAVA, JAVA_FIX, 'java', 'Greeting.missing')


def test_go_method_requires_the_receiver_in_the_qualified_name():
    pytest.importorskip('tree_sitter')
    with pytest.raises(VerificationUnavailable):
        # "Add" alone is ambiguous by contract: the receiver must be part of the target.
        transplant.transplant(GO_METHOD, GO_METHOD, 'go', 'Add')


def test_qualified_names_feed_scope_grounding():
    pytest.importorskip('tree_sitter')
    assert transplant.qualified_names(JAVA, 'java') == {'Greeting.greet'}
    assert transplant.qualified_names(GO, 'go') == {'Count'}
    assert transplant.qualified_names(GO_METHOD, 'go') == {'Counter.Add'}
    assert transplant.qualified_names(CPP, 'cpp') == {'Greeting.greet'}
    # Unsupported or unparseable input degrades to file scope, never to a guess.
    assert transplant.qualified_names('struct {', 'cpp') == set()
    assert transplant.qualified_names(JAVA, 'ruby') == set()


def test_grounding_uses_the_shared_names_for_go_and_cpp():
    pytest.importorskip('tree_sitter')
    from backend.experience.grounding import symbols
    assert symbols(GO, 'go') == {'Count'}
    assert symbols(CPP, 'cpp') == {'Greeting.greet'}


def test_template_member_is_left_to_review():
    pytest.importorskip('tree_sitter')
    template = """class Box {
public:
    template <typename T>
    T get(T value) {
        return value;
    }
};
"""
    with pytest.raises(VerificationUnavailable):
        transplant.transplant(template, template, 'cpp', 'Box.get')
