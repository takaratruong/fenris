#!/usr/bin/env python3
"""
Result Packaging Flow - Control Plane Artifact System

Standardizes packaging of benchmark/experiment results into discoverable artifacts
for cross-lane visibility in the control-plane workspace.
"""

import json
import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field, asdict
from enum import Enum


class ArtifactType(Enum):
    BENCHMARK_RESULT = "benchmark_result"
    RESEARCH_SUMMARY = "research_summary"
    EVIDENCE_COLLECTION = "evidence_collection"
    CLAIM_SUPPORT = "claim_support"
    EXPERIMENT_LOG = "experiment_log"
    PROBE_REPORT = "probe_report"


class Visibility(Enum):
    LANE_LOCAL = "lane-local"
    CROSS_LANE = "cross-lane"
    PUBLIC = "public"


@dataclass
class ArtifactMetadata:
    artifact_id: str
    artifact_type: str
    title: str
    version: str = "1.0.0"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    tags: List[str] = field(default_factory=list)
    visibility: str = "cross-lane"
    schema_version: str = "artifact-v1"
    created_by: Dict[str, str] = field(default_factory=dict)
    cross_lane_hooks: Dict[str, str] = field(default_factory=dict)


class ResultPackager:
    """Packages raw results into standardized control-plane artifacts."""
    
    CONTROL_PLANE_ROOT = Path("/home/ubuntu/.openclaw/workspace/control-plane")
    LANE_PREFIXES = {
        "research": "rs",
        "bench": "bn",
        "engineer": "en",
        "ops": "op",
        "lab": "lb",
    }
    
    def __init__(self, lane: str, agent: str = None):
        self.lane = lane
        self.agent = agent or lane
        self.workspace = self.CONTROL_PLANE_ROOT / "workspaces" / lane
        self.artifacts_dir = self.workspace / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        
    def _generate_artifact_id(self, slug: str) -> str:
        """Generate unique artifact ID: art_{lane}_{seq}_{slug}"""
        prefix = self.LANE_PREFIXES.get(self.lane, self.lane[:2])
        index = self._load_or_create_index()
        seq = str(len(index.get("artifacts", [])) + 1).zfill(3)
        return f"art_{prefix}_{seq}_{slug}"
    
    def _load_or_create_index(self) -> Dict[str, Any]:
        """Load or create the lane artifact index."""
        index_path = self.artifacts_dir / "index.json"
        if index_path.exists():
            with open(index_path) as f:
                return json.load(f)
        return {
            "index_version": "1.0.0",
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "lane": self.lane,
            "artifacts": [],
            "schema": {
                "artifact_id": "string (unique, format: art_{lane}_{seq}_{slug})",
                "artifact_type": "enum: benchmark_result | research_summary | evidence_collection | claim_support | experiment_log | probe_report",
                "visibility": "enum: lane-local | cross-lane | public",
                "tags": "string[] (for discovery)",
                "cross_lane_hooks": "object (lane -> relevance description)"
            }
        }
    
    def _save_index(self, index: Dict[str, Any]):
        """Save the artifact index."""
        index["last_updated"] = datetime.now(timezone.utc).isoformat()
        index_path = self.artifacts_dir / "index.json"
        with open(index_path, "w") as f:
            json.dump(index, f, indent=2)
    
    def package_benchmark_result(
        self,
        test_id: str,
        title: str,
        metrics: Dict[str, Any],
        task_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        tags: List[str] = None,
        cross_lane_hooks: Dict[str, str] = None,
        visibility: Visibility = Visibility.CROSS_LANE,
    ) -> Dict[str, Any]:
        """Package benchmark results into a standardized artifact."""
        
        slug = test_id.replace("-", "_").lower()[:20]
        artifact_id = self._generate_artifact_id(slug)
        
        artifact = {
            "artifact_id": artifact_id,
            "artifact_type": ArtifactType.BENCHMARK_RESULT.value,
            "version": "1.0.0",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": {
                "agent": self.agent,
                "task_id": task_id,
                "thread_id": thread_id,
            },
            "tags": tags or ["benchmark", test_id],
            "visibility": visibility.value,
            "schema_version": "artifact-v1",
            "title": title,
            "source": {
                "test_id": test_id,
                "packaged_from": "raw_metrics",
            },
            "content": {
                "metrics": metrics,
                "summary": self._generate_metrics_summary(metrics),
            },
            "cross_lane_hooks": cross_lane_hooks or {
                "research": "Benchmark data for claims validation",
                "ops": "Performance baseline reference",
                "lab": "Experiment reproducibility data",
            },
        }
        
        # Save artifact file
        artifact_path = self.artifacts_dir / f"{artifact_id}.json"
        with open(artifact_path, "w") as f:
            json.dump(artifact, f, indent=2)
        
        # Update index
        index = self._load_or_create_index()
        index["artifacts"].append({
            "artifact_id": artifact_id,
            "type": ArtifactType.BENCHMARK_RESULT.value,
            "title": title,
            "tags": artifact["tags"],
            "visibility": visibility.value,
            "path": f"artifacts/{artifact_id}.json",
            "created_at": artifact["created_at"],
            "task_id": task_id,
            "thread_id": thread_id,
        })
        self._save_index(index)
        
        return artifact
    
    def package_probe_report(
        self,
        probe_id: str,
        title: str,
        findings: Dict[str, Any],
        status: str = "complete",
        task_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        tags: List[str] = None,
    ) -> Dict[str, Any]:
        """Package probe/investigation reports."""
        
        slug = probe_id.replace("-", "_").lower()[:20]
        artifact_id = self._generate_artifact_id(slug)
        
        artifact = {
            "artifact_id": artifact_id,
            "artifact_type": ArtifactType.PROBE_REPORT.value,
            "version": "1.0.0",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": {
                "agent": self.agent,
                "task_id": task_id,
                "thread_id": thread_id,
            },
            "tags": tags or ["probe", probe_id],
            "visibility": Visibility.CROSS_LANE.value,
            "schema_version": "artifact-v1",
            "title": title,
            "status": status,
            "content": {
                "findings": findings,
            },
            "cross_lane_hooks": {
                "engineer": "Implementation considerations",
                "research": "Evidence for system behavior claims",
            },
        }
        
        artifact_path = self.artifacts_dir / f"{artifact_id}.json"
        with open(artifact_path, "w") as f:
            json.dump(artifact, f, indent=2)
        
        index = self._load_or_create_index()
        index["artifacts"].append({
            "artifact_id": artifact_id,
            "type": ArtifactType.PROBE_REPORT.value,
            "title": title,
            "tags": artifact["tags"],
            "visibility": Visibility.CROSS_LANE.value,
            "path": f"artifacts/{artifact_id}.json",
            "created_at": artifact["created_at"],
            "task_id": task_id,
            "thread_id": thread_id,
        })
        self._save_index(index)
        
        return artifact
    
    def package_evidence(
        self,
        claim_id: str,
        evidence_type: str,
        evidence_data: Dict[str, Any],
        stance: str,  # supports | weak_support | contradicts | invalidates
        task_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        tags: List[str] = None,
    ) -> Dict[str, Any]:
        """Package evidence supporting or contradicting a claim."""
        
        slug = f"ev_{claim_id[-8:]}"
        artifact_id = self._generate_artifact_id(slug)
        
        artifact = {
            "artifact_id": artifact_id,
            "artifact_type": ArtifactType.EVIDENCE_COLLECTION.value,
            "version": "1.0.0",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": {
                "agent": self.agent,
                "task_id": task_id,
                "thread_id": thread_id,
            },
            "tags": tags or ["evidence", claim_id],
            "visibility": Visibility.CROSS_LANE.value,
            "schema_version": "artifact-v1",
            "title": f"Evidence for claim {claim_id}",
            "claim_reference": {
                "claim_id": claim_id,
                "stance": stance,
            },
            "content": {
                "evidence_type": evidence_type,
                "data": evidence_data,
            },
            "cross_lane_hooks": {
                "research": "Evidence for claim evaluation",
                "lab": "Reproducibility verification",
            },
        }
        
        artifact_path = self.artifacts_dir / f"{artifact_id}.json"
        with open(artifact_path, "w") as f:
            json.dump(artifact, f, indent=2)
        
        index = self._load_or_create_index()
        index["artifacts"].append({
            "artifact_id": artifact_id,
            "type": ArtifactType.EVIDENCE_COLLECTION.value,
            "title": artifact["title"],
            "tags": artifact["tags"],
            "visibility": Visibility.CROSS_LANE.value,
            "path": f"artifacts/{artifact_id}.json",
            "created_at": artifact["created_at"],
            "task_id": task_id,
            "thread_id": thread_id,
            "claim_id": claim_id,
        })
        self._save_index(index)
        
        return artifact
    
    def _generate_metrics_summary(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a human-readable summary from metrics."""
        summary = {}
        
        if "result" in metrics:
            summary["outcome"] = metrics["result"]
        if "oom_events" in metrics:
            summary["stability"] = "stable" if metrics["oom_events"] == 0 else "unstable"
        if "errors" in metrics:
            summary["error_count"] = len(metrics.get("errors", []))
        if "phases" in metrics:
            summary["phases_completed"] = len(metrics["phases"])
            
        return summary
    
    def list_artifacts(
        self,
        artifact_type: Optional[ArtifactType] = None,
        tags: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """List artifacts with optional filtering."""
        index = self._load_or_create_index()
        artifacts = index.get("artifacts", [])
        
        if artifact_type:
            artifacts = [a for a in artifacts if a["type"] == artifact_type.value]
        
        if tags:
            artifacts = [a for a in artifacts if any(t in a.get("tags", []) for t in tags)]
        
        return artifacts
    
    def get_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a specific artifact by ID."""
        artifact_path = self.artifacts_dir / f"{artifact_id}.json"
        if artifact_path.exists():
            with open(artifact_path) as f:
                return json.load(f)
        return None


def discover_cross_lane_artifacts(
    target_lane: str = None,
    tags: List[str] = None,
) -> List[Dict[str, Any]]:
    """Discover artifacts across all lanes (or a specific lane)."""
    
    root = Path("/home/ubuntu/.openclaw/workspace/control-plane/workspaces")
    results = []
    
    lanes = [target_lane] if target_lane else [d.name for d in root.iterdir() if d.is_dir()]
    
    for lane in lanes:
        index_path = root / lane / "artifacts" / "index.json"
        if index_path.exists():
            with open(index_path) as f:
                index = json.load(f)
            
            for artifact in index.get("artifacts", []):
                if artifact.get("visibility") in ["cross-lane", "public"]:
                    if tags is None or any(t in artifact.get("tags", []) for t in tags):
                        artifact["_source_lane"] = lane
                        results.append(artifact)
    
    return results


if __name__ == "__main__":
    # Demo: Package the existing fenris stress test
    packager = ResultPackager(lane="bench", agent="bench")
    
    # Load existing metrics
    metrics_path = Path("/home/ubuntu/.openclaw/workspace/control-plane/workspaces/bench/fenris-stress-test-3/artifacts/metrics.json")
    if metrics_path.exists():
        with open(metrics_path) as f:
            metrics = json.load(f)
        
        artifact = packager.package_benchmark_result(
            test_id="fenris-stress-test-3",
            title="Fenris AWS Infrastructure Stress Test #3",
            metrics=metrics,
            tags=["fenris", "stress-test", "aws", "infrastructure"],
        )
        print(f"Created artifact: {artifact['artifact_id']}")
        print(json.dumps(artifact, indent=2))
