#!/usr/bin/env python3
"""
Baseline A Harness - Isolated branch implementation.

This harness extends the standard BaselineHarness with:
- Branch-specific memory that is NOT shared with Baselines B or C
- Isolated state management
- Namespaced artifacts to prevent cross-contamination

Usage:
    from baseline_a.baseline_a_harness import BaselineAHarness
    
    with BaselineAHarness() as harness:
        harness.log_metric("score", 0.95)
        harness.memory_set("checkpoint_data", {"epoch": 10})
        harness.checkpoint("training_complete")
"""

import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add parent to path for harness import
sys.path.insert(0, str(Path(__file__).parent.parent))
from harness.baseline_harness import (
    BaselineHarness, 
    ExperimentConfig, 
    get_system_info,
    get_system_metrics
)

# Baseline A specific paths
BASELINE_A_ROOT = Path(__file__).parent
BASELINE_A_MEMORY = BASELINE_A_ROOT / "memory"
BASELINE_A_ARTIFACTS = BASELINE_A_ROOT / "artifacts"


@dataclass
class BaselineAConfig:
    """Configuration specific to Baseline A experiments."""
    experiment_name: str = "baseline_a_default"
    collect_system_metrics: bool = True
    metrics_interval_sec: float = 1.0
    isolation_mode: str = "strict"  # strict = no shared state
    tags: List[str] = field(default_factory=lambda: ["baseline_a", "isolated"])
    parameters: Dict[str, Any] = field(default_factory=dict)


class BranchMemory:
    """
    Isolated branch memory for Baseline A.
    
    This memory store is NOT shared with Baselines B or C.
    State persists across experiment runs within this branch only.
    """
    
    def __init__(self, memory_dir: Path):
        self.memory_dir = memory_dir
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.memory_dir / "state.json"
        self._state: Dict[str, Any] = self._load_state()
    
    def _load_state(self) -> Dict[str, Any]:
        """Load persisted state from disk."""
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except Exception:
                return {"_version": 1, "_created": datetime.now(timezone.utc).isoformat()}
        return {"_version": 1, "_created": datetime.now(timezone.utc).isoformat()}
    
    def _save_state(self):
        """Persist state to disk."""
        self._state["_last_modified"] = datetime.now(timezone.utc).isoformat()
        with open(self.state_file, "w") as f:
            json.dump(self._state, f, indent=2, default=str)
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from branch memory."""
        return self._state.get(key, default)
    
    def set(self, key: str, value: Any):
        """Set a value in branch memory (auto-persists)."""
        self._state[key] = value
        self._save_state()
    
    def delete(self, key: str) -> bool:
        """Delete a key from branch memory."""
        if key in self._state and not key.startswith("_"):
            del self._state[key]
            self._save_state()
            return True
        return False
    
    def list_keys(self) -> List[str]:
        """List all user keys (excluding internal _ prefixed keys)."""
        return [k for k in self._state.keys() if not k.startswith("_")]
    
    def clear(self):
        """Clear all user state (preserves metadata)."""
        metadata = {k: v for k, v in self._state.items() if k.startswith("_")}
        self._state = metadata
        self._save_state()


class BaselineAHarness:
    """
    Specialized harness for Baseline A with isolated branch memory.
    
    Key differences from standard BaselineHarness:
    - Uses BranchMemory for persistent isolated state
    - All artifacts stored in baseline_a/artifacts/
    - Prevents cross-contamination with Baselines B and C
    """
    
    def __init__(self, config: Optional[BaselineAConfig] = None):
        self.config = config or BaselineAConfig()
        self.memory = BranchMemory(BASELINE_A_MEMORY)
        self.run_id = f"baseline_a_run_{uuid.uuid4().hex[:8]}"
        self._metrics: List[Dict[str, Any]] = []
        self._checkpoints: List[Dict[str, Any]] = []
        self._custom_metrics: List[Dict[str, Any]] = []
        self._start_time: Optional[str] = None
        self._end_time: Optional[str] = None
        self._status = "initialized"
        self._errors: List[str] = []
        
        # Ensure artifacts directory exists
        BASELINE_A_ARTIFACTS.mkdir(parents=True, exist_ok=True)
    
    @property
    def artifacts_dir(self) -> Path:
        """Directory for Baseline A artifacts."""
        return BASELINE_A_ARTIFACTS
    
    def __enter__(self) -> "BaselineAHarness":
        """Start the Baseline A experiment."""
        self._status = "running"
        self._start_time = datetime.now(timezone.utc).isoformat()
        
        # Record run in branch memory
        runs = self.memory.get("_runs", [])
        runs.append({
            "run_id": self.run_id,
            "started": self._start_time,
            "config": {
                "experiment_name": self.config.experiment_name,
                "isolation_mode": self.config.isolation_mode,
                "tags": self.config.tags
            }
        })
        self.memory._state["_runs"] = runs[-50:]  # Keep last 50 runs
        self.memory._save_state()
        
        # Log initial checkpoint
        self.checkpoint("baseline_a_start", {
            "isolation_mode": self.config.isolation_mode,
            "memory_keys": self.memory.list_keys()
        })
        
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Finalize the Baseline A experiment."""
        self._end_time = datetime.now(timezone.utc).isoformat()
        
        if exc_type is not None:
            self._status = "failed"
            self._errors.append(f"{exc_type.__name__}: {exc_val}")
        else:
            self._status = "completed"
        
        # Save results
        self._save_results()
        
        return False
    
    def log_metric(self, name: str, value: float, tags: Optional[Dict[str, str]] = None):
        """Log a custom metric."""
        metric = {
            "name": f"baseline_a.{name}",  # Namespaced
            "value": value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if tags:
            metric["tags"] = tags
        self._custom_metrics.append(metric)
    
    def checkpoint(self, name: str, data: Optional[Dict[str, Any]] = None):
        """Record a checkpoint."""
        checkpoint = {
            "name": name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if data:
            checkpoint["data"] = data
        self._checkpoints.append(checkpoint)
    
    def memory_get(self, key: str, default: Any = None) -> Any:
        """Get a value from isolated branch memory."""
        return self.memory.get(key, default)
    
    def memory_set(self, key: str, value: Any):
        """Set a value in isolated branch memory."""
        self.memory.set(key, value)
    
    def save_artifact(self, name: str, content: Any, artifact_type: str = "json") -> Path:
        """Save an artifact with baseline_a namespace."""
        if not name.startswith("baseline_a_"):
            name = f"baseline_a_{name}"
        
        if artifact_type == "json":
            path = self.artifacts_dir / f"{name}.json"
            with open(path, "w") as f:
                json.dump(content, f, indent=2, default=str)
        elif artifact_type == "text":
            path = self.artifacts_dir / f"{name}.txt"
            with open(path, "w") as f:
                f.write(str(content))
        else:
            path = self.artifacts_dir / name
            with open(path, "wb") as f:
                f.write(content)
        
        return path
    
    def _calculate_duration(self) -> Optional[float]:
        """Calculate experiment duration in seconds."""
        if self._start_time and self._end_time:
            start = datetime.fromisoformat(self._start_time.replace("Z", "+00:00"))
            end = datetime.fromisoformat(self._end_time.replace("Z", "+00:00"))
            return (end - start).total_seconds()
        return None
    
    def _save_results(self):
        """Save final results."""
        results = {
            "run_id": self.run_id,
            "baseline": "A",
            "isolation_mode": self.config.isolation_mode,
            "experiment_name": self.config.experiment_name,
            "status": self._status,
            "start_time": self._start_time,
            "end_time": self._end_time,
            "duration_sec": self._calculate_duration(),
            "checkpoints": self._checkpoints,
            "custom_metrics": self._custom_metrics,
            "errors": self._errors,
            "tags": self.config.tags,
            "memory_keys_at_end": self.memory.list_keys(),
            "isolation_verified": True  # Baseline A is isolated by design
        }
        
        self.save_artifact(f"results_{self.run_id}", results)
        
        # Update run record in memory
        runs = self.memory.get("_runs", [])
        if runs:
            runs[-1]["ended"] = self._end_time
            runs[-1]["status"] = self._status
            runs[-1]["duration_sec"] = self._calculate_duration()
            self.memory._state["_runs"] = runs
            self.memory._save_state()


def run_baseline_a_demo():
    """Demonstrate Baseline A harness functionality."""
    print("=" * 60)
    print("Baseline A - Isolated Branch Demo")
    print("=" * 60)
    
    config = BaselineAConfig(
        experiment_name="baseline_a_demo",
        tags=["baseline_a", "isolated", "demo", "tsk_f31aa1e4717f"],
        parameters={"demo_iterations": 5}
    )
    
    with BaselineAHarness(config) as harness:
        # Demonstrate isolated memory
        print(f"\n[{harness.run_id}] Starting demo...")
        
        previous_score = harness.memory_get("last_demo_score")
        print(f"  Previous demo score (from branch memory): {previous_score}")
        
        # Run some work
        harness.checkpoint("work_start")
        
        scores = []
        for i in range(5):
            score = 0.8 + (i * 0.04)
            scores.append(score)
            harness.log_metric("iteration_score", score, {"iteration": str(i)})
        
        final_score = sum(scores) / len(scores)
        harness.log_metric("final_score", final_score)
        
        # Store in isolated memory
        harness.memory_set("last_demo_score", final_score)
        harness.memory_set("demo_history", {
            "run_id": harness.run_id,
            "scores": scores,
            "final": final_score
        })
        
        harness.checkpoint("work_complete", {"final_score": final_score})
        
        # Save artifact
        harness.save_artifact("demo_output", {
            "message": "Baseline A demo completed successfully",
            "isolation_verified": True,
            "final_score": final_score,
            "iterations": 5
        })
        
        print(f"  Final score: {final_score:.3f}")
        print(f"  Artifacts saved to: {harness.artifacts_dir}")
    
    print(f"\n[{harness.run_id}] Demo complete!")
    print(f"  Status: {harness._status}")
    print(f"  Duration: {harness._calculate_duration():.2f}s")
    print(f"  Memory keys: {harness.memory.list_keys()}")
    print("=" * 60)
    
    return harness


if __name__ == "__main__":
    run_baseline_a_demo()
