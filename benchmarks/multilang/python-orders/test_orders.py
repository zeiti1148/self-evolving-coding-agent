from orders import total


def test_total_of_basic_basket():
    assert total([10, 20.5]) == 30.5


def test_total_of_empty_basket_is_zero():
    assert total([]) == 0
