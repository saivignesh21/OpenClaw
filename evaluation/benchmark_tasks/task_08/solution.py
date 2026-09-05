def normalize_name(name):
    return ' '.join(part.capitalize() for part in name.split(' ') if part)
