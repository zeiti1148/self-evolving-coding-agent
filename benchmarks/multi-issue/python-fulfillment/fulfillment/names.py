def recipient_name(name):
    # Deliberate defect: a missing recipient is dereferenced.
    return name.strip()
