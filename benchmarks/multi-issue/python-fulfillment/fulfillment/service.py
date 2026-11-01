from fulfillment.items import select_item
from fulfillment.names import recipient_name
from fulfillment.quantities import parse_quantity


def prepare(name, quantity, items, index):
    return f"{recipient_name(name)}|{parse_quantity(quantity)}|{select_item(items, index)}"
