from .names import normalize
from .quantities import parse
from .items import select

def prepare(name, quantity, values, index):
    return f"{normalize(name)}|{parse(quantity)}|{select(values, index)}"
