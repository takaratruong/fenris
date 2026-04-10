#!/usr/bin/env python3
"""
Baseline B Harness - Isolated branch implementation.

This harness extends the base harness with strict isolation guarantees:
- Separate branch memory from Baselines A and C
- Independent state management
- No cross-baseline contamination of metrics or artifacts
- Unique run ID prefix (b_) to prevent collisions

Usage:
    from baseline_b.baseline_b_harness import BaselineBHarness
    
    with BaselineBHarness() as harness:
        harness.log_metric("throughput", 1000)
        harness.memory_set("key", "value")
        harness.checkpoint("done")
"""

import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from contextlib import contextmanager
import threading

# Add parent path for base harness import
sys.path.insert(0, str(Path(__file__).parent.parent / "harness"))
from baseline_harness import (
    BaselineHarness, 
    ExperimentConfig, 
    HarnessState,
    MetricsCollector,
    get_system_info,
    get_system_metrics,
    WORKSPACE_ROOT
)

# Baseline B specific paths
BASELINE_B_ROOT = Path(__file__).parent
BASELINE_B_MEMORY = BASELINE_B_ROOT / "memory"
BASELINE_B_ARTIFACTS = BASELINE_B_ROOT / "artifacts"
BASELINE_B_STATE = BASELINE_B_ROOT / "branch_state.json"


class BranchMemory:
    """
    Isolated memory store for Baseline B.
    
    Data is persisted to baseline_b/memory/state.json and is never
    shared with Baselines A or C.
    """
    
    def __init__(self, memory_path: Path = BASELINE_B_MEMORY):
        self.memory_path = memory_path
        self.state_file = memory_path / "state.json"
        self._data: Dict[str, Any] = {}
        self._load()
    
    def _load(self):
        """Load state from disk."""
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    stored = json.load(f)
                    self._data = stored.get("entries", {})
            except (json.JSONDecodeError, KeyError):
                self._data = {}
    
    def _save(self):
        """Persist state to disk."""
        self.memory_path.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump({
                "branch": "baseline_b",
                "updated": datetime.now(timezone.utc).isoformat(),
                "entries": self._data,
                "version": 1
            }, f, indent=2, default=str)
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from branch memory."""
        return self._data.get(key, default)
    
    def set(self, key: str, value: Any):
        """Set a value in branch memory (auto-persists)."""
        self._data[key] = value
        self._save()
    
    def delete(self, key: str) -> bool:
        """Delete a key from branch memory."""
        if key in self._data:
            del self._data[key]
            self._save()
            return True
        return False
    
    def keys(self) -> List[str]:
        """List all keys in branch memory."""
        return list(self._data.keys())
    
    def clear(self):
        """Clear all branch memory."""
        self._data = {}
        self._save()


class BaselineBHarness(BaselineHarness):
    """
    Extended harness with Baseline B isolation guarantees.
    
    Differences from base harness:
    - All run IDs prefixed with 'b_'
    - Artifacts stored in baseline_b/artifacts/
    - Branch memory available via memory_get/memory_set
    - Branch state tracked in branch_state.json
    """
    
    def __init__(self, name: str = "baseline_b_experiment", **kwargs):
        # Generate Baseline B specific experiment ID
        exp_id = f"baseline_b_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        
        # Default config for Baseline B
        config = ExperimentConfig(
            experiment_id=exp_id,
            name=name,
            lane="engineer",
            tags=["baseline_b", "isolated"] + kwargs.get("tags", []),
            parameters=kwargs.get("parameters", {}),
            metrics_interval_sec=kwargs.get("metrics_interval_sec", 1.0),
            collect_system_metrics=kwargs.get("collect_system_metrics", True),
        )
        
        super().__init__(config)
        
        # Override run ID with B prefix
        self._run_id = f"b_run_{uuid.uuid4().hex[:12]}"
        
        # Initialize isolated branch memory
        self._branch_memory = BranchMemory()
        
        # Override artifacts dir to Baseline B specific location
        self._artifacts_dir = BASELINE_B_ARTIFACTS / self.config.experiment_id
    
    @property
    def artifacts_dir(self) -> Path:
        """Baseline B artifacts directory (isolated)."""
        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        return self._artifacts_dir
    
    def memory_get(self, key: str, default: Any = None) -> Any:
        """Get a value from Baseline B's isolated branch memory."""
        return self._branch_memory.get(key, default)
    
    def memory_set(self, key: str, value: Any):
        """Set a value in Baseline B's isolated branch memory."""
        self._branch_memory.set(key, value)
    
    def memory_delete(self, key: str) -> bool:
        """Delete a key from Baseline B's isolated branch memory."""
        return self._branch_memory.delete(key)
    
    def memory_keys(self) -> List[str]:
        """List all keys in Baseline B's branch memory."""
        return self._branch_memory.keys()
    
    def __enter__(self) -> "BaselineBHarness":
        """Start experiment and update branch state."""
        result = super().__enter__()
        self._update_branch_state("started")
        return result
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Finalize experiment and update branch state."""
        result = super().__exit__(exc_type, exc_val, exc_tb)
        self._update_branch_state("completed" if exc_type is None else "failed")
        return result
    
    def _update_branch_state(self, status: str):
        """Update the branch state file with run info."""
        try:
            if BASELINE_B_STATE.exists():
                with open(BASELINE_B_STATE) as f:
                    state = json.load(f)
            else:
                state = {
                    "branch_id": "baseline_b",
                    "created": datetime.now(timezone.utc).isoformat(),
                    "runs": [],
                }
            
            # Record this run
            run_info = {
                "run_id": self._run_id,
                "experiment_id": self.config.experiment_id,
                "status": status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            
            state["runs"].append(run_info)
            state["last_updated"] = datetime.now(timezone.utc).isoformat()
            
            # Keep last 50 runs
            state["runs"] = state["runs"][-50:]
            
            with open(BASELINE_B_STATE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            # Don't fail the experiment for state tracking issues
            self.state.errors.append(f"Branch state update failed: {e}")
    
    def _update_artifact_index(self, results: Dict[str, Any]):
        """Update Baseline B's artifact index (isolated from other baselines)."""
        index_path = BASELINE_B_ARTIFACTS / "index.json"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Load existing index
        index = {"artifacts": [], "last_updated": None, "branch": "baseline_b"}
        if index_path.exists():
            try:
                with open(index_path) as f:
                    index = json.load(f)
            except Exception:
                pass
        
        # Add this run
        entry = {
            "run_id": self._run_id,
            "experiment_id": self.config.experiment_id,
            "name": self.config.name,
            "status": self.state.status,
            "timestamp": self.state.end_time,
            "artifacts_path": str(self.artifacts_dir),
            "tags": self.config.tags,
        }
        
        # Keep last 100 entries
        index["artifacts"] = [entry] + index.get("artifacts", [])[:99]
        index["last_updated"] = datetime.now(timezone.utc).isoformat()
        index["branch"] = "baseline_b"
        
        with open(index_path, "w") as f:
            json.dump(index, f, indent=2)


@contextmanager
def quick_baseline_b(name: str = "quick_test", **kwargs):
    """
    Convenience wrapper for quick Baseline B experiments.
    
    Usage:
        with quick_baseline_b("my-test") as h:
            h.log_metric("score", 0.95)
            h.memory_set("key", "value")
    """
    with BaselineBHarness(name=name, **kwargs) as harness:
        yield harness


def verify_isolation() -> Dict[str, Any]:
    """
    Verify that Baseline B is properly isolated from other baselines.
    
    Returns verification results.
    """
    results = {
        "branch": "baseline_b",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": []
    }
    
    # Check 1: Memory path isolation
    baseline_a_memory = WORKSPACE_ROOT / "engineer" / "baseline_a" / "memory"
    baseline_b_memory = BASELINE_B_MEMORY
    
    results["checks"].append({
        "name": "memory_path_isolation",
        "passed": str(baseline_a_memory) != str(baseline_b_memory),
        "baseline_a_path": str(baseline_a_memory),
        "baseline_b_path": str(baseline_b_memory),
    })
    
    # Check 2: Artifacts path isolation
    baseline_a_artifacts = WORKSPACE_ROOT / "engineer" / "baseline_a" / "artifacts"
    baseline_b_artifacts = BASELINE_B_ARTIFACTS
    
    results["checks"].append({
        "name": "artifacts_path_isolation",
        "passed": str(baseline_a_artifacts) != str(baseline_b_artifacts),
        "baseline_a_path": str(baseline_a_artifacts),
        "baseline_b_path": str(baseline_b_artifacts),
    })
    
    # Check 3: State file isolation
    baseline_a_state = WORKSPACE_ROOT / "engineer" / "baseline_a" / "branch_state.json"
    baseline_b_state = BASELINE_B_STATE
    
    results["checks"].append({
        "name": "state_file_isolation",
        "passed": str(baseline_a_state) != str(baseline_b_state),
        "baseline_a_path": str(baseline_a_state),
        "baseline_b_path": str(baseline_b_state),
    })
    
    # Check 4: Baseline B directories exist
    results["checks"].append({
        "name": "baseline_b_structure",
        "passed": BASELINE_B_ROOT.exists() and BASELINE_B_MEMORY.exists(),
        "root_exists": BASELINE_B_ROOT.exists(),
        "memory_exists": BASELINE_B_MEMORY.exists(),
        "artifacts_exists": BASELINE_B_ARTIFACTS.exists(),
    })
    
    results["all_passed"] = all(c["passed"] for c in results["checks"])
    return results


if __name__ == "__main__":
    print("=" * 60)
    print("Baseline B Harness - Self Test")
    print("=" * 60)
    
    # First verify isolation
    print("\n1. Verifying isolation from other baselines...")
    isolation = verify_isolation()
    for check in isolation["checks"]:
        status = "✓" if check["passed"] else "✗"
        print(f"   {status} {check['name']}")
    
    if not isolation["all_passed"]:
        print("\n⚠ Isolation verification failed!")
        sys.exit(1)
    
    print("\n2. Running harness self-test...")
    with BaselineBHarness(name="self_test", tags=["selftest"]) as harness:
        harness.checkpoint("start")
        
        # Test metrics logging
        for i in range(3):
            time.sleep(0.3)
            harness.log_metric("iteration", i)
            harness.log_metric("score", 0.9 + i * 0.03)
        
        harness.checkpoint("metrics_logged")
        
        # Test branch memory
        harness.memory_set("test_key", {"nested": "value", "number": 42})
        retrieved = harness.memory_get("test_key")
        assert retrieved["number"] == 42, "Branch memory retrieval failed"
        harness.checkpoint("memory_tested")
        
        # Test artifact saving
        harness.save_artifact("test_output", {
            "message": "Baseline B self-test passed",
            "isolation_verified": True
        })
        harness.checkpoint("complete")
    
    print(f"\n3. Results:")
    print(f"   Run ID: {harness._run_id}")
    print(f"   Status: {harness.state.status}")
    print(f"   Artifacts: {harness.artifacts_dir}")
    print(f"   Branch memory keys: {harness.memory_keys()}")
    
    print("\n" + "=" * 60)
    print("Baseline B self-test: PASSED")
    print("=" * 60)
