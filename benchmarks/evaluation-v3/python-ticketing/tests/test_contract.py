import pytest
from app.names import normalize_name
from app.quantities import parse_quantity
from app.items import select_item
from app.service import prepare


def test_name_missing():
    assert normalize_name(None) == "attendee"


def test_name_blank():
    assert normalize_name("  ") == "attendee"


def test_name_normal():
    assert normalize_name(" Ada ") == "Ada"


def test_quantity_missing():
    assert parse_quantity(None) == 2
    assert parse_quantity(" ") == 2


def test_quantity_format():
    assert parse_quantity("bad") == 2


def test_quantity_bounds():
    assert parse_quantity("-1") == 1
    assert parse_quantity("99") == 6


def test_quantity_normal():
    assert parse_quantity(" 4 ") == 4


def test_items_missing():
    assert select_item(None, 0) == "waitlist"


def test_items_empty():
    assert select_item([], 0) == "waitlist"


def test_items_bounds():
    assert select_item(["vip"], -1) == "waitlist"
    assert select_item(["vip"], 1) == "waitlist"


def test_items_normal():
    assert select_item(["a", "b"], 1) == "b"


def test_combined_service():
    assert prepare(None, "99", ["vip"], 5) == "attendee:6:waitlist"
