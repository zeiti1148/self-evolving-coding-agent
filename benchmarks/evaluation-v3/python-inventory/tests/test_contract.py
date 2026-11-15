import pytest
from app.names import normalize_name
from app.quantities import parse_quantity
from app.items import select_item
from app.service import prepare


def test_name_missing():
    with pytest.raises(ValueError):
        normalize_name(None)


def test_name_blank():
    with pytest.raises(ValueError):
        normalize_name(" ")


def test_name_normal():
    assert normalize_name(" sku-1 ") == "SKU-1"


def test_quantity_missing():
    assert parse_quantity(None) == 0
    assert parse_quantity(" ") == 0


def test_quantity_format():
    with pytest.raises(ValueError):
        parse_quantity("bad")


def test_quantity_bounds():
    for value in ("-1", "501"):
        with pytest.raises(ValueError):
            parse_quantity(value)


def test_quantity_normal():
    assert parse_quantity(" 10 ") == 10


def test_items_missing():
    assert select_item(None, 0) == "backorder"


def test_items_empty():
    assert select_item([], 0) == "backorder"


def test_items_bounds():
    assert select_item(["first", "last"], -1) == "first"
    assert select_item(["first"], 1) == "first"


def test_items_normal():
    assert select_item(["a", "b"], 1) == "b"


def test_combined_service():
    assert prepare(" sku-1 ", None, ["first", "last"], -1) == "SKU-1:0:first"
