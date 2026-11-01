import pytest
from checkout.names import normalize
from checkout.quantities import parse
from checkout.items import select
from checkout.service import prepare

def test_missing_name():
    assert normalize(None) == "guest"

def test_blank_name():
    assert normalize("  ") == "guest"

def test_empty_name():
    assert normalize("") == "guest"

def test_whitespace_name():
    assert normalize("\t\n") == "guest"

def test_missing_number():
    assert parse(None) == 0

def test_malformed_number():
    assert parse("bad") == 0

def test_negative_number():
    assert parse("-2") == 0

def test_huge_number():
    assert parse("1000") == 0

def test_missing_collection():
    assert select(None, 0) == "none"

def test_empty_collection():
    assert select([], 0) == "none"

def test_negative_index():
    assert select(["book"], -1) == "none"

def test_upper_boundary():
    assert select(["book"], 1) == "none"

def test_normal_name():
    assert normalize(" Ada ") == "Ada"

def test_normal_number():
    assert parse(" 3 ") == 3

def test_normal_selection():
    assert select(["book", "pen"], 1) == "pen"

def test_combined_missing():
    assert prepare(None, "bad", None, 0) == "guest|0|none"

def test_combined_boundary():
    assert prepare("", "-1", [], 0) == "guest|0|none"

def test_combined_normal():
    assert prepare(" Ada ", "2", ["book"], 0) == "Ada|2|book"
