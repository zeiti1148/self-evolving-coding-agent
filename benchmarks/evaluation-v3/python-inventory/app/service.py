from .names import normalize_name
from .quantities import parse_quantity
from .items import select_item

def prepare(name, quantity, items, index):
    return f"{normalize_name(name)}:{parse_quantity(quantity)}:{select_item(items, index)}"
