def parse_quantity(text):
    # Deliberate defect: invalid inputs are not normalized to zero.
    return int(text)
