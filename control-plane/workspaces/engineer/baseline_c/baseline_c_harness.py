#!/usr/bin/env python3
"""
Baseline C Harness - Shared context evaluation framework.

Unlike Baselines A/B (isolated), Baseline C supports multiple methods
evaluating against shared state and contributing results collaboratively.
"""

import json
import os
import fcntl
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from contextlib import contextmanager

# Import base harness
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from harness.baseline_harness import BaselineHarness, ExperimentConfig, get_system_metrics


BASELINE_C_ROOT = Path(__file__).parent
SHARED_STATE_FILE = BASELINE_C_ROOT / "shared_state.json"
SHARED_CONTEXT_FILE = BASELINE_C_ROOT / "memory" / "shared_context.json"


def atomic_json_read(path: Path) -> Dict[str, Any]:
    """Read JSON with file locking for concurrent access."""
    if not path.exists():
        return {}
    with open(path, 'r') as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
        try:
            return json.load(f)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def atomic_json_write(path: Path, data: Dict[str, Any]):
    """Write JSON with file locking for atomic updates."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            json.dump(data, f, indent=2, default=str)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def atomic_json_update(path: Path, updates: Dict[str, Any]):
    """Atomically update specific keys in a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Use exclusive lock for read-modify-write
    with open(path, 'r+' if path.exists() else 'w+') as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.seek(0)
            content = f.read()
            data = json.loads(content) if content else {}
            
            # Deep merge updates
            for key, value in updates.items():
                if isinstance(value, dict) and isinstance(data.get(key), dict):
                    data[key].update(value)
                else:
                    data[key] = value
            
            f.seek(0)
            f.truncate()
            json.dump(data, f, indent=2, default=str)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class BaselineCHarness:
    """
    Harness for Baseline C with shared context support.
    
    Enables multiple methods to:
    - Read shared context from other methods
    - Contribute their own results to shared state
    - Coordinate via cross-method signals
    """
    
    def __init__(self, method_id: str, task_id: Optional[str] = None):
        self.method_id = method_id
        self.task_id = task_id
        self.run_id = f"c_{method_id}_{uuid.uuid4().hex[:8]}"
        self.start_time: Optional[str] = None
        self.end_time: Optional[str] = None
        self._metrics: List[Dict[str, Any]] = []
        self._checkpoints: List[Dict[str, Any]] = []
        self._artifacts_dir = BASELINE_C_ROOT / "artifacts" / "method_results" / method_id
        self._status = "initialized"
        
    @property
    def artifacts_dir(self) -> Path:
        """Directory for method-specific artifacts."""
        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        return self._artifacts_dir
    
    def __enter__(self) -> "BaselineCHarness":
        """Start method evaluation."""
        self.start_time = datetime.now(timezone.utc).isoformat()
        self._status = "running"
        
        # Register method start in shared state
        atomic_json_update(SHARED_STATE_FILE, {
            "methods": {
                self.method_id: {
                    "status": "running",
                    "started_at": self.start_time,
                    "run_id": self.run_id
                }
            }
        })
        
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Finalize method evaluation."""
        self.end_time = datetime.now(timezone.utc).isoformat()
        
        if exc_type is not None:
            self._status = "failed"
            error_msg = f"{exc_type.__name__}: {exc_val}"
        else:
            self._status = "completed"
            error_msg = None
        
        # Update shared state with completion
        atomic_json_update(SHARED_STATE_FILE, {
            "methods": {
                self.method_id: {
                    "status": self._status,
                    "ended_at": self.end_time,
                    "error": error_msg
                }
            }
        })
        
        # Save method results
        self._save_results()
        
        return False
    
    def get_shared_context(self) -> Dict[str, Any]:
        """Read the shared context accessible by all methods."""
        return atomic_json_read(SHARED_CONTEXT_FILE)
    
    def update_shared_context(self, updates: Dict[str, Any]):
        """Contribute updates to the shared context."""
        # Wrap updates under this method's namespace
        namespaced_updates = {
            "method_contributions": {
                self.method_id: {
                    **updates,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
            }
        }
        atomic_json_update(SHARED_CONTEXT_FILE, namespaced_updates)
    
    def add_cross_method_signal(self, signal_type: str, data: Dict[str, Any]):
        """Add a signal for other methods to observe."""
        context = atomic_json_read(SHARED_CONTEXT_FILE)
        signals = context.get("cross_method_signals", [])
        signals.append({
            "type": signal_type,
            "from_method": self.method_id,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        atomic_json_update(SHARED_CONTEXT_FILE, {"cross_method_signals": signals})
    
    def get_other_method_results(self) -> Dict[str, Any]:
        """Get results from other methods (if completed)."""
        state = atomic_json_read(SHARED_STATE_FILE)
        results = {}
        for method, info in state.get("methods", {}).items():
            if method != self.method_id and info.get("status") == "completed":
                # Load their results if available
                results_path = BASELINE_C_ROOT / "artifacts" / "method_results" / method / "results.json"
                if results_path.exists():
                    results[method] = atomic_json_read(results_path)
        return results
    
    def log_metric(self, name: str, value: float, tags: Optional[Dict[str, str]] = None):
        """Log a method-specific metric."""
        metric = {
            "name": name,
            "value": value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "method": self.method_id
        }
        if tags:
            metric["tags"] = tags
        self._metrics.append(metric)
    
    def checkpoint(self, name: str, data: Optional[Dict[str, Any]] = None):
        """Record a checkpoint in the method evaluation."""
        checkpoint = {
            "name": name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "method": self.method_id
        }
        if data:
            checkpoint["data"] = data
        self._checkpoints.append(checkpoint)
    
    def save_artifact(self, name: str, content: Any, artifact_type: str = "json") -> Path:
        """Save a method-specific artifact."""
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
    
    def _save_results(self):
        """Save final method results."""
        duration = None
        if self.start_time and self.end_time:
            start = datetime.fromisoformat(self.start_time.replace("Z", "+00:00"))
            end = datetime.fromisoformat(self.end_time.replace("Z", "+00:00"))
            duration = (end - start).total_seconds()
        
        results = {
            "run_id": self.run_id,
            "method_id": self.method_id,
            "task_id": self.task_id,
            "baseline": "baseline_c",
            "status": self._status,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_sec": duration,
            "checkpoints": self._checkpoints,
            "metrics": self._metrics,
            "system_metrics": get_system_metrics()
        }
        self.save_artifact("results", results)


@contextmanager  
def quick_baseline_c(method_id: str):
    """Convenience wrapper for quick method evaluations."""
    with BaselineCHarness(method_id=method_id) as harness:
        yield harness


if __name__ == "__main__":
    # Self-test
    print("Running Baseline C harness self-test...")
    
    with BaselineCHarness(method_id="selftest", task_id="tsk_test") as harness:
        harness.checkpoint("start")
        
        # Check shared context
        ctx = harness.get_shared_context()
        print(f"Shared context keys: {list(ctx.keys())}")
        
        # Log some metrics
        for i in range(3):
            harness.log_metric("iteration", i)
            harness.log_metric("score", 0.8 + i * 0.05)
        
        # Contribute to shared context
        harness.update_shared_context({
            "selftest_completed": True,
            "final_score": 0.9
        })
        
        harness.checkpoint("complete")
        harness.save_artifact("test_output", {"message": "Self-test passed"})
    
    print(f"Self-test completed. Status: {harness._status}")
    print(f"Artifacts at: {harness.artifacts_dir}")
