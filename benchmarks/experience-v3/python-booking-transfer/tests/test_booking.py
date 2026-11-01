import pytest
from booking.names import normalize
from booking.quantities import parse
from booking.items import select
from booking.service import prepare

def test_missing_name():
    assert normalize(None) == 'visitor'

def test_blank_name():
    assert normalize("  ") == 'visitor'

def test_valid_name():
    assert normalize(" Ada ") == "Ada"

def test_valid_number():
    assert parse(" 2 ") == 2

def test_malformed_number():
    assert parse("bad") == 1

def test_missing_number():
    assert parse(None) == 1

def test_valid_selection():
    assert select(["room", "desk"], 1) == "desk"

def test_upper_boundary():
    assert select(["room"], 1) == 'unassigned'

def test_missing_collection():
    assert select(None, 0) == 'unassigned'

def test_combined_missing():
    assert prepare(None, "bad", None, 0) == "visitor|1|unassigned"

def test_combined_blank():
    assert prepare("", "2", [], 0) == "visitor|2|unassigned"

def test_combined_negative_quantity_is_rejected():
    with pytest.raises(ValueError):
        prepare(" Ada ", "-1", ["room"], 0)
