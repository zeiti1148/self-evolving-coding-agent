def select_item(items, index):
    # Deliberate defect: missing and negative indexes are accepted or raised.
    return items[index]
