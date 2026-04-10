#!/usr/bin/env python3
"""
Metrics Collection Library for Control-Plane
Task: tsk_fd473154869d | Thread: thr_1621f695c75d

Provides programmatic access to metrics across all lanes.
"""

import json
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

CONTROL_PLANE_ROOT = Path(__file__).parent.parent.parent.parent


@dataclass
class MetricPoint:
    """A single metric data point."""
    lane: str
    source_file: str
    timestamp: str
    data: Dict[str, Any]
    tags: List[str]


@dataclass
class CollectionResult:
    """Result of a metrics collection run."""
    collection_id: str
    timestamp: str
    lanes: Dict[str, Dict[str, Any]]
    summary: Dict[str, int]
    metrics: List[MetricPoint]


class MetricsCollector:
    """Collects and indexes metrics from control-plane lanes."""
    
    LANES = ['bench', 'research', 'engineer', 'ops', 'lab', 'chief_of_staff', 'orchestrator']
    
    def __init__(self, root: Optional[Path] = None):
        self.root = root or CONTROL_PLANE_ROOT
        self.workspaces = self.root / 'workspaces'
    
    def collect_lane(self, lane: str) -> Dict[str, Any]:
        """Collect metrics from a single lane."""
        lane_dir = self.workspaces / lane
        if not lane_dir.exists():
            return {'exists': False}
        
        result = {
            'exists': True,
            'metrics_files': [],
            'indexed_artifacts': 0,
            'latest_metric': None,
        }
        
        # Find all metrics.json files
        for metrics_file in lane_dir.rglob('metrics.json'):
            result['metrics_files'].append(str(metrics_file))
            result['latest_metric'] = str(metrics_file)
        
        # Check artifact index
        index_file = lane_dir / 'artifacts' / 'index.json'
        if index_file.exists():
            try:
                with open(index_file) as f:
                    index_data = json.load(f)
                result['indexed_artifacts'] = len(index_data.get('artifacts', []))
            except (json.JSONDecodeError, KeyError):
                pass
        
        return result
    
    def collect_all(self) -> CollectionResult:
        """Collect metrics from all lanes."""
        now = datetime.now(timezone.utc)
        timestamp = now.isoformat().replace('+00:00', 'Z')
        collection_id = f"col_{int(now.timestamp())}"
        
        lanes = {}
        all_metrics = []
        
        for lane in self.LANES:
            lane_result = self.collect_lane(lane)
            if lane_result.get('exists'):
                lanes[lane] = {
                    'metrics_files': len(lane_result['metrics_files']),
                    'indexed_artifacts': lane_result['indexed_artifacts'],
                    'latest_metric': lane_result['latest_metric'],
                }
                
                # Load actual metric data
                for mf in lane_result['metrics_files']:
                    try:
                        with open(mf) as f:
                            data = json.load(f)
                        all_metrics.append(MetricPoint(
                            lane=lane,
                            source_file=mf,
                            timestamp=data.get('timestamp', timestamp),
                            data=data,
                            tags=data.get('tags', []),
                        ))
                    except (json.JSONDecodeError, OSError):
                        pass
        
        summary = {
            'total_metrics_files': sum(l.get('metrics_files', 0) for l in lanes.values()),
            'total_indexed_artifacts': sum(l.get('indexed_artifacts', 0) for l in lanes.values()),
            'active_lanes': len([l for l in lanes.values() if l.get('metrics_files', 0) > 0 or l.get('indexed_artifacts', 0) > 0]),
            'lanes_scanned': len(lanes),
        }
        
        return CollectionResult(
            collection_id=collection_id,
            timestamp=timestamp,
            lanes=lanes,
            summary=summary,
            metrics=all_metrics,
        )
    
    def save_collection(self, result: CollectionResult, output_dir: Optional[Path] = None) -> Path:
        """Save collection result to JSON file."""
        output_dir = output_dir or (self.workspaces / 'engineer' / 'metrics')
        output_dir.mkdir(parents=True, exist_ok=True)
        
        filename = f"collected-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
        output_file = output_dir / filename
        
        # Convert to JSON-serializable dict
        data = {
            'collection_id': result.collection_id,
            'timestamp': result.timestamp,
            'lanes': result.lanes,
            'summary': result.summary,
            'metrics': [asdict(m) for m in result.metrics],
        }
        
        with open(output_file, 'w') as f:
            json.dump(data, f, indent=2)
        
        return output_file
    
    def get_latest_collection(self) -> Optional[Dict[str, Any]]:
        """Get the most recent collection result."""
        metrics_dir = self.workspaces / 'engineer' / 'metrics'
        if not metrics_dir.exists():
            return None
        
        collection_files = sorted(metrics_dir.glob('collected-*.json'), reverse=True)
        if not collection_files:
            return None
        
        with open(collection_files[0]) as f:
            return json.load(f)
    
    def query_metrics(self, lane: Optional[str] = None, tag: Optional[str] = None) -> List[Dict[str, Any]]:
        """Query collected metrics with optional filters."""
        latest = self.get_latest_collection()
        if not latest:
            return []
        
        metrics = latest.get('metrics', [])
        
        if lane:
            metrics = [m for m in metrics if m.get('lane') == lane]
        
        if tag:
            metrics = [m for m in metrics if tag in m.get('tags', [])]
        
        return metrics


def main():
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Collect metrics from control-plane lanes')
    parser.add_argument('--lane', help='Collect from specific lane only')
    parser.add_argument('--query', action='store_true', help='Query latest collection instead of collecting')
    parser.add_argument('--tag', help='Filter by tag (with --query)')
    parser.add_argument('--json', action='store_true', help='Output as JSON')
    
    args = parser.parse_args()
    collector = MetricsCollector()
    
    if args.query:
        if args.lane or args.tag:
            results = collector.query_metrics(lane=args.lane, tag=args.tag)
        else:
            results = collector.get_latest_collection()
        
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            if isinstance(results, list):
                print(f"Found {len(results)} metrics")
                for m in results:
                    print(f"  - {m.get('lane')}: {m.get('source_file')}")
            elif results:
                print(f"Collection: {results.get('collection_id')}")
                print(f"Timestamp: {results.get('timestamp')}")
                print(f"Summary: {results.get('summary')}")
    else:
        result = collector.collect_all()
        output_file = collector.save_collection(result)
        
        if args.json:
            print(json.dumps(asdict(result) if hasattr(result, '__dict__') else {
                'collection_id': result.collection_id,
                'timestamp': result.timestamp,
                'lanes': result.lanes,
                'summary': result.summary,
            }, indent=2))
        else:
            print(f"Collection complete: {result.collection_id}")
            print(f"Output: {output_file}")
            print(f"Summary: {result.summary}")


if __name__ == '__main__':
    main()
