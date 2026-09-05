def count_vowels(text):
    return sum(ch in 'aeiou' for ch in text.lower()) + 1
