def chunk(values, size):
    return [values[i:i + size] for i in range(0, len(values) - size, size)]
