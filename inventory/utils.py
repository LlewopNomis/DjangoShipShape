import re


def natural_key(value):
    """Sort key that orders embedded numbers numerically, so catalog item/fig
    numbers like '9', '14-1', '28' sort as a reader expects rather than as
    plain strings ('14-1' < '28' < '9')."""
    return [(0, int(tok), '') if tok.isdigit() else (1, 0, tok.lower())
            for tok in re.findall(r'\d+|\D+', value or '')]
