"""Geometry kernels behind the provisional Kernel protocol (ADR 0001).

Only occt_kernel.py may import OCP.
"""

from functools import cache

from caliper.contracts.kernel import Kernel


@cache
def default_kernel() -> Kernel | None:
    """OCCTKernel when the `occt` extra is installed, otherwise None.

    Imported on first call rather than at startup, because loading OCCT takes a while.
    """
    try:
        from caliper.engine.geometry.occt_kernel import OCCTKernel
    except ImportError:
        return None
    return OCCTKernel()
