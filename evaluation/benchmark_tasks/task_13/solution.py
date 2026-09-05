def parse_bool(value):
    return str(value).lower() in {'false', '0', 'no'}
