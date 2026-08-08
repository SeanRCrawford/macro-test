"""Maximum-weight bipartite matching (the assignment problem).

Kept separate from anything Pokemon-shaped so it can be tested against cases
with hand-checkable answers. Exact, via a bitmask DP over columns: O(n * 2^m)
time, which at our sizes (at most 6x6) is a few thousand operations.

No scipy. `scipy.optimize.linear_sum_assignment` would do this in one call, but
the dependency is not installed and the DP below is short enough that adding it
is not worth it.
"""


def max_weight_matching(weights):
    """Assign each ROW to a distinct COLUMN so total weight is maximised.

    `weights[i][j]` is the value of assigning row i to column j. Every row is
    matched if there are enough columns; when rows outnumber columns the
    surplus rows are left unmatched and contribute nothing. Weights may be
    negative -- a forced bad assignment is still an assignment, which is the
    point when the question is "what is my best answer to this threat, even if
    my best answer is poor".

    Returns (total_weight, pairs) with pairs a list of (row, column).
    """
    n = len(weights)
    if n == 0:
        return 0.0, []
    m = len(weights[0])
    if m == 0:
        return 0.0, []

    NEG = float("-inf")
    # best[mask] = (value, pairs) for the rows seen so far, using exactly the
    # columns in `mask`.
    best = {0: (0.0, ())}
    for row in range(n):
        nxt = {}
        for mask, (value, pairs) in best.items():
            # Skipping a row is allowed so that surplus rows have somewhere to
            # go when rows outnumber columns. It must NOT become a way to dodge
            # an unattractive assignment, so the size filter below forces the
            # matching to be as large as possible -- without it, all-negative
            # weights would "optimise" to matching nothing, and coverage would
            # silently report no threats instead of no answers.
            keep = nxt.get(mask, (NEG, ()))
            if value > keep[0]:
                nxt[mask] = (value, pairs)
            for col in range(m):
                bit = 1 << col
                if mask & bit:
                    continue
                new_mask = mask | bit
                new_value = value + weights[row][col]
                cur = nxt.get(new_mask, (NEG, ()))
                if new_value > cur[0]:
                    nxt[new_mask] = (new_value, pairs + ((row, col),))
        best = nxt

    size = min(n, m)
    candidates = [entry for mask, entry in best.items()
                  if bin(mask).count("1") == size]
    value, pairs = max(candidates, key=lambda x: x[0])
    return value, list(pairs)
