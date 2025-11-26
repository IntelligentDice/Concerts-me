from fuzzywuzzy import fuzz

def fuzzy_compare(a, b):
    return fuzz.ratio(a.lower(), b.lower())
