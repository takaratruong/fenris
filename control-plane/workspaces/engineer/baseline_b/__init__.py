"""
Baseline B - Isolated Branch Implementation

This module provides a harness for running experiments in an isolated
branch that does not share state with Baselines A or C.

Usage:
    from baseline_b import BaselineBHarness, quick_baseline_b
    
    # Full control
    with BaselineBHarness(name="my-experiment") as harness:
        harness.log_metric("throughput", 1000)
        harness.memory_set("key", "value")
    
    # Quick experiments
    with quick_baseline_b("quick-test") as h:
        h.log_metric("score", 0.95)
"""

from .baseline_b_harness import (
    BaselineBHarness,
    BranchMemory,
    quick_baseline_b,
    verify_isolation,
    BASELINE_B_ROOT,
    BASELINE_B_MEMORY,
    BASELINE_B_ARTIFACTS,
    BASELINE_B_STATE,
)

__all__ = [
    "BaselineBHarness",
    "BranchMemory",
    "quick_baseline_b",
    "verify_isolation",
    "BASELINE_B_ROOT",
    "BASELINE_B_MEMORY",
    "BASELINE_B_ARTIFACTS",
    "BASELINE_B_STATE",
]
