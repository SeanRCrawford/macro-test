"""Two-player zero-sum matrix game solving.

Deliberately knows NOTHING about Pokemon. The only thing it needs from a caller
is a `payoff(i, j)` oracle returning the row player's value for the pure joint
action (i, j); this module decides which cells to ask for. That separation is
what lets the game logic be tested against textbook games with known answers
(see tests/test_matrix_game.py) rather than only against battle simulations.

Two solvers:

  solve_matrix()  -- regret matching over a FULLY materialised matrix. Exact
                     enough at our sizes, no scipy dependency.
  double_oracle() -- solves a large game by materialising only a small subgame,
                     calling `payoff` lazily.

IMPORTANT COST NOTE (measured, see tools/measure_depth2_cost.py):
double oracle does NOT reduce the payoff-evaluation count to the size of the
final restricted matrix. Each iteration's best-response step must scan the
FULL action set of one player against the current mixed strategy of the other,
which costs |actions| x |support| evaluations. The restricted matrix ends up
small (equilibrium support really is 2-5 actions), but getting there is not
free. Callers budgeting for "double oracle = a handful of cells" will be wrong;
budget with `DoubleOracleResult.n_evals`.
"""
from dataclasses import dataclass, field


# Iterations for the small scalar path. A 4x4 game is converged long before
# 3000 sweeps -- measured value error against the 3000-iteration answer is
# ~0.07 points at 800, which is a rounding error next to KO_WEIGHT = 180 and
# far under the 2.0-point threshold below which a punish is not even reported.
# Verified against tools/golden_baseline.py: not one of the 33 pinned turns
# changes its chosen action.
SMALL_ITERS = 800

try:
    import numpy as _np
except ImportError:   # pragma: no cover
    _np = None

def solve_matrix(A, iters: int = 3000):
    """Nash equilibrium of zero-sum matrix `A` by regret matching.

    `A[i][j]` is the ROW player's payoff; the row player maximises and the
    column player minimises. Returns (value, p, q) with p/q the row/column
    mixed strategies as probability lists.

    Regret matching converges to the equilibrium of a zero-sum game in the
    average strategy (not the current one), so both are accumulated and the
    averages returned.

    PERFORMANCE. Profiling one audited line put 69% of the whole run inside
    this function: the two utility lines are matrix-vector products (A @ q and
    A.T @ p) that were written as nested Python sums, so a single 30x60 turn
    cost 15 million generator calls. numpy does the same arithmetic in one
    call. The scalar path below is kept for tiny matrices, where the array
    conversion costs more than it saves, and as the readable definition of what
    the vectorised branch is doing.

    The 64-cell threshold is measured, not guessed (3000 iterations each):

        size     scalar     numpy   speedup
        2x2       0.019     0.043      0.4x
        4x4       0.032     0.052      0.6x
        6x6       0.047     0.052      0.9x
        8x8       0.067     0.056      1.2x   <- crossover, 64 cells
        12x12     0.129     0.061      2.1x
        30x60     0.839     0.069     12.2x

    Below it numpy's per-call overhead dominates, and the nested subgames the
    double oracle solves are exactly that small -- so both branches earn their
    place. End to end this took one audited line from 15.8s to 2.7s, with the
    golden baseline unchanged.
    """
    n, m = len(A), len(A[0])
    if _np is not None and n * m >= 64:
        return _solve_matrix_np(A, iters)
    return _solve_matrix_py(A, min(iters, SMALL_ITERS))


def _solve_matrix_np(A, iters):
    """Vectorised regret matching. Same algorithm, same iteration count."""
    mat = _np.asarray(A, dtype=_np.float64)
    n, m = mat.shape
    regret_p = _np.zeros(n)
    regret_q = _np.zeros(m)
    sum_p = _np.zeros(n)
    sum_q = _np.zeros(m)

    for _ in range(iters):
        p = _match_np(regret_p)
        q = _match_np(regret_q)
        sum_p += p
        sum_q += q
        util_p = mat @ q
        util_q = -(p @ mat)
        regret_p += util_p - float(p @ util_p)
        regret_q += util_q - float(q @ util_q)

    p = _normalise_np(sum_p)
    q = _normalise_np(sum_q)
    value = float(p @ mat @ q)
    return value, p.tolist(), q.tolist()


def _match_np(regrets):
    pos = _np.maximum(regrets, 0.0)
    total = pos.sum()
    if total <= 0.0:
        return _np.full(regrets.shape, 1.0 / regrets.size)
    return pos / total


def _normalise_np(weights):
    total = weights.sum()
    if total <= 0.0:
        return _np.full(weights.shape, 1.0 / weights.size)
    return weights / total


def _solve_matrix_py(A, iters):
    """The scalar reference implementation. See solve_matrix."""
    n, m = len(A), len(A[0])
    regret_p = [0.0] * n
    regret_q = [0.0] * m
    sum_p = [0.0] * n
    sum_q = [0.0] * m

    for _ in range(iters):
        p = _regret_match(regret_p)
        q = _regret_match(regret_q)
        for i in range(n):
            sum_p[i] += p[i]
        for j in range(m):
            sum_q[j] += q[j]

        # Value of each pure action against the opponent's current mix.
        # The column player minimises the row payoff, so its utility is negated.
        util_p = [sum(A[i][j] * q[j] for j in range(m)) for i in range(n)]
        util_q = [-sum(A[i][j] * p[i] for i in range(n)) for j in range(m)]
        val_p = sum(p[i] * util_p[i] for i in range(n))
        val_q = sum(q[j] * util_q[j] for j in range(m))
        for i in range(n):
            regret_p[i] += util_p[i] - val_p
        for j in range(m):
            regret_q[j] += util_q[j] - val_q

    p = _normalise(sum_p)
    q = _normalise(sum_q)
    value = sum(p[i] * A[i][j] * q[j] for i in range(n) for j in range(m))
    return value, p, q


def _regret_match(regrets):
    """Strategy proportional to positive regret; uniform if there is none."""
    pos = [r if r > 0.0 else 0.0 for r in regrets]
    total = sum(pos)
    if total <= 0.0:
        return [1.0 / len(regrets)] * len(regrets)
    return [x / total for x in pos]


def _normalise(weights):
    total = sum(weights)
    if total <= 0.0:
        return [1.0 / len(weights)] * len(weights)
    return [x / total for x in weights]


@dataclass
class DoubleOracleResult:
    value: float
    # Mixed strategies over the FULL action sets (zero outside the support).
    p: list
    q: list
    rows: list              # restricted row indices, in the order added
    cols: list              # restricted column indices, in the order added
    n_evals: int            # distinct payoff(i, j) calls actually made
    iterations: int
    converged: bool
    cache: dict = field(default_factory=dict, repr=False)

    def support(self, tol: float = 1e-3):
        """(row indices, column indices) carrying meaningful probability."""
        return ([i for i, x in enumerate(self.p) if x > tol],
                [j for j, x in enumerate(self.q) if x > tol])


def double_oracle(n_rows: int, n_cols: int, payoff,
                  seed_rows=None, seed_cols=None,
                  max_iters: int = 20, tol: float = 1e-6,
                  inner_iters: int = 3000):
    """Equilibrium of a large zero-sum game, materialising a small subgame.

    `payoff(i, j)` -> float is called lazily and memoised; `n_evals` in the
    result counts distinct calls, which is the honest cost measure since in our
    setting one call means one simulated turn.

    Seeding matters. `seed_rows` / `seed_cols` should be the actions a cheap
    heuristic already believes in (for us: the greedy pick and Protect, and
    later the actions justified by threat-matrix pressure edges) -- a good seed
    is the difference between converging in two iterations and five.

    On convergence the restricted equilibrium is an equilibrium of the FULL
    game, so no accuracy is traded for the pruning. If `max_iters` is hit first
    the result is a lower bound on the row player's security level and
    `converged` is False -- callers that care should check it rather than
    assume.
    """
    cache = {}

    def value_at(i, j):
        key = (i, j)
        if key not in cache:
            cache[key] = payoff(i, j)
        return cache[key]

    rows = list(dict.fromkeys(seed_rows if seed_rows else [0]))
    cols = list(dict.fromkeys(seed_cols if seed_cols else [0]))

    converged = False
    iterations = 0
    p_full = [0.0] * n_rows
    q_full = [0.0] * n_cols
    value = 0.0

    for iterations in range(1, max_iters + 1):
        sub = [[value_at(i, j) for j in cols] for i in rows]
        value, p_sub, q_sub = solve_matrix(sub, iters=inner_iters)

        p_full = [0.0] * n_rows
        for idx, i in enumerate(rows):
            p_full[i] = p_sub[idx]
        q_full = [0.0] * n_cols
        for idx, j in enumerate(cols):
            q_full[j] = q_sub[idx]

        # Best response for each side against the other's current mix. Only the
        # support carries weight, so only those columns/rows need evaluating --
        # this is where the saving comes from, and also why the cost is
        # |actions| x |support| rather than O(1).
        live_cols = [(j, q_sub[idx]) for idx, j in enumerate(cols) if q_sub[idx] > 1e-9]
        live_rows = [(i, p_sub[idx]) for idx, i in enumerate(rows) if p_sub[idx] > 1e-9]

        br_row, br_row_val = None, float("-inf")
        for i in range(n_rows):
            v = sum(w * value_at(i, j) for j, w in live_cols)
            if v > br_row_val:
                br_row, br_row_val = i, v

        br_col, br_col_val = None, float("inf")
        for j in range(n_cols):
            v = sum(w * value_at(i, j) for i, w in live_rows)
            if v < br_col_val:
                br_col, br_col_val = j, v

        # Converged when neither side can profitably leave the restricted game.
        row_gain = br_row_val - value
        col_gain = value - br_col_val
        if row_gain <= tol and col_gain <= tol:
            converged = True
            break

        added = False
        if br_row is not None and br_row not in rows and row_gain > tol:
            rows.append(br_row)
            added = True
        if br_col is not None and br_col not in cols and col_gain > tol:
            cols.append(br_col)
            added = True
        if not added:
            # Both best responses are already in the restricted sets, but the
            # gaps did not close -- treat as converged rather than spinning.
            converged = True
            break

    return DoubleOracleResult(value=value, p=p_full, q=q_full, rows=rows, cols=cols,
                              n_evals=len(cache), iterations=iterations,
                              converged=converged, cache=cache)


def maximin(A):
    """Value and index of the best PURE row (the safest single action)."""
    worst = [min(row) for row in A]
    best = max(worst)
    return best, worst.index(best)
