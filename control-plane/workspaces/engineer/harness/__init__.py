"""Baseline Harness - Experiment execution framework for control-plane lanes."""

from .baseline_harness import (
    BaselineHarness,
    ExperimentConfig,
    HarnessState,
    MetricsCollector,
    quick_harness,
    get_system_info,
    get_system_metrics,
    WORKSPACE_ROOT,
)

__all__ = [
    "BaselineHarness",
    "ExperimentConfig", 
    "HarnessState",
    "MetricsCollector",
    "quick_harness",
    "get_system_info",
    "get_system_metrics",
    "WORKSPACE_ROOT",
]

__version__ = "1.0.0"
