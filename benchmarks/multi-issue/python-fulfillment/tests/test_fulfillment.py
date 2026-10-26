from fulfillment.items import select_item
from fulfillment.names import recipient_name
from fulfillment.quantities import parse_quantity
from fulfillment.service import prepare


def test_name_is_trimmed():
    assert recipient_name(" Alice ") == "Alice"


def test_missing_name_uses_guest():
    assert recipient_name(None) == "guest"


def test_normal_quantity():
    assert parse_quantity(" 3 ") == 3


def test_invalid_quantity():
    assert parse_quantity("three") == 0


def test_blank_quantity():
    assert parse_quantity(" ") == 0


def test_missing_quantity():
    assert parse_quantity(None) == 0


def test_negative_quantity():
    assert parse_quantity("-2") == 0


def test_normal_selection():
    assert select_item(["book", "pen"], 1) == "pen"


def test_empty_selection():
    assert select_item([], 0) == "none"


def test_out_of_range_selection():
    assert select_item(["book"], 1) == "none"


def test_negative_selection():
    assert select_item(["book"], -1) == "none"


def test_combined_request():
    assert prepare(None, "bad", [], 2) == "guest|0|none"
