"""Cap BLAS threading. MUST be imported before numpy or pandas.

WHY THIS EXISTS. OpenBLAS (under numpy, under pandas) builds a thread pool
sized to the machine's core count when it is first loaded, and allocates
per-thread buffers for it. That is harmless in one process and quadratic
across a process pool: `--jobs 0` on a 16-core machine gives 16 workers each
spinning up 16 OpenBLAS threads, so 256 thread pools' worth of buffers. The
symptom is not a clean out-of-memory but

    OpenBLAS error: Memory Allocation still failed after 10 retries, giving up

which is OpenBLAS failing to carve out its own workspace, reported from
whichever worker happened to lose the race.

Nothing here is numerically parallel anyway -- the equilibrium solver is pure
Python and pandas is used once at startup to parse spreadsheets -- so a single
BLAS thread per process costs nothing and removes the failure entirely. The
parallelism that matters is our own, one process per pairing.

IMPORT ORDER IS LOad-BEARING. These variables are read once, when the BLAS
library is loaded, so setting them after `import pandas` does nothing at all.
Import this module before anything that reaches numpy, and keep it first in
the import block even though a formatter will want to sort it lower -- hence
the noqa markers on the imports that follow it in the entry points.
"""
import os

# setdefault, not assignment: a user who deliberately exports one of these to
# tune their own run should win over this default.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
             "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_var, "1")


def workers_advice(requested, cpu_count=None):
    """(workers, warning_or_None) for a requested --jobs value.

    Memory, not CPU, is what limits this search: every worker holds its own
    copy of the parsed dataset. Rather than silently capping, which would hide
    why a run is slower than the flag implies, this returns the number asked
    for plus a warning the caller can print.
    """
    cpu_count = cpu_count or os.cpu_count() or 1
    workers = cpu_count if requested == 0 else max(1, requested)
    warning = None
    if workers > 8:
        warning = (f"{workers} workers will each hold a copy of the dataset "
                   f"(~1GB). If this machine has less than ~{workers}GB of "
                   f"free RAM, pass --jobs 8 or fewer.")
    return workers, warning
