"""Server-side code scope extraction. Parser failures never imply disjoint scopes."""
from pathlib import Path


def symbols(source: str, language: str) -> set[str]:
    from tree_sitter import Language, Parser
    if language == 'python':
        import tree_sitter_python as grammar
        definitions = {'function_definition', 'class_definition'}
    elif language == 'java':
        import tree_sitter_java as grammar
        definitions = {'method_declaration', 'constructor_declaration', 'class_declaration', 'interface_declaration'}
    else:
        return set()  # Unsupported grammars retain conservative file scope.
    tree = Parser(Language(grammar.language())).parse(source.encode('utf-8'))
    if tree.root_node.has_error:
        return set()
    found = set()
    def visit(node, parents):
        current = parents
        if node.type in definitions:
            name = node.child_by_field_name('name')
            if name:
                current = [*parents, name.text.decode('utf-8')]
                if node.type not in {'class_definition', 'class_declaration', 'interface_declaration'}:
                    found.add('.'.join(current))
        for child in node.named_children:
            visit(child, current)
    visit(tree.root_node, [])
    return found


def ground_issues(issues, workspace, language):
    """Unverifiable symbol hints degrade to file scope; they never discard raw knowledge."""
    root = Path(workspace).resolve()
    for issue in issues:
        actual = set()
        for name in issue.files:
            path = root / name
            try:
                if path.is_symlink() or not path.resolve().is_relative_to(root) or path.stat().st_size > 2_000_000:
                    continue
                actual.update(f'{name}#{symbol}' for symbol in symbols(path.read_text(encoding='utf-8'), language))
            except (OSError, UnicodeError, ImportError, ValueError):
                continue
        requested = set(issue.symbols)
        if requested and not requested.issubset(actual):
            issue.symbols = []
            continue
        issue.symbols = sorted(requested or (actual if len(actual) == 1 else set()))
