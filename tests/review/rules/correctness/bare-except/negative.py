def parse(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
