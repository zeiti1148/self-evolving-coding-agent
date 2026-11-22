from greeting import greet


def test_greet_keeps_named_input():
    assert greet("Ann") == "Hello, Ann!"


def test_greet_defaults_empty_name():
    assert greet("") == "Hello, guest!"
