"""Grounding keeps a symbol hint that names a real target, and drops invented ones."""
from backend.experience.grounding import ground_issues
from backend.shared import RepairIssue

GO = '''package greeting

func Greet(name string) string {
\treturn "Hello, " + name + "!"
}
'''

TWO = '''package greeting

func Greet(name string) string { return name }
func Farewell(name string) string { return name }
'''


def issue(symbols):
    return RepairIssue(error_type='AssertionError', root_cause='missing', solution='default guest',
                       files=['greeting.go'], symbols=symbols)


def test_bare_symbol_hint_is_resolved_to_the_parsed_target(tmp_path):
    (tmp_path / 'greeting.go').write_text(GO, encoding='utf-8')
    value = issue(['Greet'])
    ground_issues([value], tmp_path, 'go')
    assert value.symbols == ['greeting.go#Greet']


def test_wrong_file_prefix_is_dropped_instead_of_moved(tmp_path):
    (tmp_path / 'greeting.go').write_text(GO, encoding='utf-8')
    value = issue(['other.go#Greet'])
    ground_issues([value], tmp_path, 'go')
    assert value.symbols == []


def test_invented_symbol_is_still_dropped(tmp_path):
    (tmp_path / 'greeting.go').write_text(GO, encoding='utf-8')
    value = issue(['missing.go#Unknown'])
    ground_issues([value], tmp_path, 'go')
    assert value.symbols == []


JAVA = '''class A { void Greet() {} }
class B { void Greet() {} }
'''


def test_bare_name_resolves_even_when_the_file_has_several_functions(tmp_path):
    (tmp_path / 'greeting.go').write_text(TWO, encoding='utf-8')
    value = issue(['Greet'])
    ground_issues([value], tmp_path, 'go')
    assert value.symbols == ['greeting.go#Greet']


def test_bare_name_matching_two_qualified_methods_is_dropped(tmp_path):
    (tmp_path / 'Demo.java').write_text(JAVA, encoding='utf-8')
    value = RepairIssue(error_type='NullPointerException', root_cause='missing', solution='default',
                        files=['Demo.java'], symbols=['Greet'])
    ground_issues([value], tmp_path, 'java')
    assert value.symbols == []
