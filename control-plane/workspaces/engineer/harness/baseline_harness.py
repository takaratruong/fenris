#!/usr/bin/env python3
"""
Baseline Harness - Standardized experiment execution framework.

Provides:
- Consistent environment setup and teardown
- Metrics collection (system + custom)
- Artifact generation and indexing
- Reproducibility through config snapshots
- Integration with control-plane lanes

Usage:
    from harness.baseline_harness import BaselineHarness, ExperimentConfig
    
    config = ExperimentConfig(
        experiment_id="exp_001",
        name="attention-benchmark",
        lane="bench",
        metrics_interval_sec=1.0
    )
    
    with BaselineHarness(config) as harness:
        # Your experiment code
        harness.log_metric("throughput", 1234.5)
        harness.checkpoint("phase_1_complete")
"""

import json
import os
import platform
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from contextlib import contextmanager
import threading
import traceback

# Control plane workspace root
WORKSPACE_ROOT = Path("/home/ubuntu/.openclaw/workspace/control-plane/workspaces")


@dataclass
class ExperimentConfig:
    """Configuration for a harness-managed experiment."""
    experiment_id: str
    name: str
    lane: str = "engineer"  # Which agent lane owns this
    metrics_interval_sec: float = 1.0
    timeout_sec: Optional[float] = None
    tags: List[str] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    collect_system_metrics: bool = True
    artifact_retention_days: int = 30


@dataclass 
class HarnessState:
    """Runtime state of the harness."""
    status: str = "initialized"  # initialized, running, completed, failed, timeout
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    checkpoints: List[Dict[str, Any]] = field(default_factory=list)
    metrics: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    

def get_system_info() -> Dict[str, Any]:
    """Collect system information for reproducibility."""
    info = {
        "hostname": platform.node(),
        "platform": platform.platform(),
        "python_version": sys.version,
        "cpu_count": os.cpu_count(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    
    # Memory info
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    info["mem_total_kb"] = int(line.split()[1])
                    break
    except Exception:
        pass
    
    # GPU info if available
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            info["gpus"] = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
    except Exception:
        info["gpus"] = []
    
    return info


def get_system_metrics() -> Dict[str, float]:
    """Collect current system metrics."""
    metrics = {}
    
    # CPU usage from /proc/stat
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
            metrics["load_1m"] = float(parts[0])
            metrics["load_5m"] = float(parts[1])
            metrics["load_15m"] = float(parts[2])
    except Exception:
        pass
    
    # Memory from /proc/meminfo
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    meminfo[parts[0].rstrip(":")] = int(parts[1])
            
            total = meminfo.get("MemTotal", 1)
            available = meminfo.get("MemAvailable", total)
            metrics["mem_used_percent"] = 100 * (1 - available / total)
    except Exception:
        pass
    
    return metrics


class MetricsCollector:
    """Background thread for periodic metrics collection."""
    
    def __init__(self, interval_sec: float, callback: Callable[[Dict[str, float]], None]):
        self.interval_sec = interval_sec
        self.callback = callback
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
    
    def start(self):
        self._thread = threading.Thread(target=self._collect_loop, daemon=True)
        self._thread.start()
    
    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
    
    def _collect_loop(self):
        while not self._stop_event.wait(self.interval_sec):
            try:
                metrics = get_system_metrics()
                metrics["timestamp"] = datetime.now(timezone.utc).isoformat()
                self.callback(metrics)
            except Exception:
                pass


class BaselineHarness:
    """
    Main harness class for running experiments with standardized infrastructure.
    
    Use as a context manager to ensure proper setup and teardown.
    """
    
    def __init__(self, config: ExperimentConfig):
        self.config = config
        self.state = HarnessState()
        self.system_info = get_system_info()
        self._metrics_collector: Optional[MetricsCollector] = None
        self._custom_metrics: List[Dict[str, Any]] = []
        self._artifacts_dir: Optional[Path] = None
        self._run_id = f"run_{uuid.uuid4().hex[:12]}"
    
    @property
    def artifacts_dir(self) -> Path:
        """Directory for storing experiment artifacts."""
        if self._artifacts_dir is None:
            self._artifacts_dir = (
                WORKSPACE_ROOT / self.config.lane / 
                self.config.experiment_id / "artifacts"
            )
            self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        return self._artifacts_dir
    
    def __enter__(self) -> "BaselineHarness":
        """Start the experiment."""
        self.state.status = "running"
        self.state.start_time = datetime.now(timezone.utc).isoformat()
        
        # Start metrics collection
        if self.config.collect_system_metrics:
            self._metrics_collector = MetricsCollector(
                self.config.metrics_interval_sec,
                lambda m: self.state.metrics.append(m)
            )
            self._metrics_collector.start()
        
        # Save config snapshot
        self._save_config_snapshot()
        
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Finalize the experiment."""
        # Stop metrics collection
        if self._metrics_collector:
            self._metrics_collector.stop()
        
        self.state.end_time = datetime.now(timezone.utc).isoformat()
        
        if exc_type is not None:
            self.state.status = "failed"
            self.state.errors.append(f"{exc_type.__name__}: {exc_val}")
            self.state.errors.append(traceback.format_exc())
        else:
            self.state.status = "completed"
        
        # Save final results
        self._save_results()
        
        return False  # Don't suppress exceptions
    
    def log_metric(self, name: str, value: float, tags: Optional[Dict[str, str]] = None):
        """Log a custom metric."""
        metric = {
            "name": name,
            "value": value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if tags:
            metric["tags"] = tags
        self._custom_metrics.append(metric)
    
    def checkpoint(self, name: str, data: Optional[Dict[str, Any]] = None):
        """Record a checkpoint in the experiment."""
        checkpoint = {
            "name": name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if data:
            checkpoint["data"] = data
        self.state.checkpoints.append(checkpoint)
    
    def save_artifact(self, name: str, content: Any, artifact_type: str = "json") -> Path:
        """
        Save an artifact to the experiment directory.
        
        Args:
            name: Artifact filename (without extension)
            content: Data to save
            artifact_type: 'json', 'text', or 'binary'
        
        Returns:
            Path to the saved artifact
        """
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
    
    def _save_config_snapshot(self):
        """Save experiment configuration for reproducibility."""
        snapshot = {
            "run_id": self._run_id,
            "config": asdict(self.config),
            "system_info": self.system_info,
            "env_snapshot": {
                k: v for k, v in os.environ.items()
                if k.startswith(("CUDA", "XLA", "JAX", "TF", "TORCH", "OMP", "MKL"))
            }
        }
        self.save_artifact("config_snapshot", snapshot)
    
    def _save_results(self):
        """Save final experiment results."""
        results = {
            "run_id": self._run_id,
            "experiment_id": self.config.experiment_id,
            "name": self.config.name,
            "lane": self.config.lane,
            "status": self.state.status,
            "start_time": self.state.start_time,
            "end_time": self.state.end_time,
            "duration_sec": self._calculate_duration(),
            "checkpoints": self.state.checkpoints,
            "custom_metrics": self._custom_metrics,
            "system_metrics_count": len(self.state.metrics),
            "errors": self.state.errors,
            "tags": self.config.tags,
        }
        self.save_artifact("results", results)
        
        # Save detailed system metrics separately (can be large)
        if self.state.metrics:
            self.save_artifact("system_metrics", self.state.metrics)
        
        # Create index entry for cross-lane discovery
        self._update_artifact_index(results)
    
    def _calculate_duration(self) -> Optional[float]:
        """Calculate experiment duration in seconds."""
        if self.state.start_time and self.state.end_time:
            start = datetime.fromisoformat(self.state.start_time.replace("Z", "+00:00"))
            end = datetime.fromisoformat(self.state.end_time.replace("Z", "+00:00"))
            return (end - start).total_seconds()
        return None
    
    def _update_artifact_index(self, results: Dict[str, Any]):
        """Update the lane's artifact index for cross-lane discovery."""
        index_path = WORKSPACE_ROOT / self.config.lane / "artifacts" / "index.json"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Load existing index
        index = {"artifacts": [], "last_updated": None}
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
        
        with open(index_path, "w") as f:
            json.dump(index, f, indent=2)


@contextmanager
def quick_harness(name: str, lane: str = "engineer", **kwargs):
    """
    Convenience wrapper for simple experiments.
    
    Usage:
        with quick_harness("my-test") as h:
            h.log_metric("score", 0.95)
    """
    exp_id = f"exp_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    config = ExperimentConfig(
        experiment_id=exp_id,
        name=name,
        lane=lane,
        **kwargs
    )
    with BaselineHarness(config) as harness:
        yield harness


if __name__ == "__main__":
    # Self-test
    print("Running baseline harness self-test...")
    
    config = ExperimentConfig(
        experiment_id="harness_selftest",
        name="baseline-harness-validation",
        lane="engineer",
        tags=["selftest", "validation"],
        parameters={"test_iterations": 3}
    )
    
    with BaselineHarness(config) as harness:
        harness.checkpoint("start")
        
        for i in range(3):
            time.sleep(0.5)
            harness.log_metric("iteration", i)
            harness.log_metric("score", 0.9 + i * 0.03)
        
        harness.checkpoint("iterations_complete", {"total": 3})
        harness.save_artifact("test_output", {"message": "Self-test passed"})
    
    print(f"Self-test completed. Artifacts at: {harness.artifacts_dir}")
    print(f"Status: {harness.state.status}")
