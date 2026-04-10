#!/usr/bin/env python3
"""
CLI for Result Packaging Flow

Usage:
  package_result.py benchmark --lane LANE --test-id ID --title TITLE --metrics-file FILE [--task-id TID] [--thread-id THR] [--tags TAG,TAG]
  package_result.py probe --lane LANE --probe-id ID --title TITLE --findings-file FILE [--task-id TID] [--thread-id THR]
  package_result.py evidence --lane LANE --claim-id CID --stance STANCE --evidence-file FILE [--task-id TID] [--thread-id THR]
  package_result.py list --lane LANE [--type TYPE] [--tags TAG,TAG]
  package_result.py discover [--lane LANE] [--tags TAG,TAG]
"""

import argparse
import json
import sys
from pathlib import Path

# Import from sibling module
sys.path.insert(0, str(Path(__file__).parent))
from result_packaging import ResultPackager, ArtifactType, Visibility, discover_cross_lane_artifacts


def cmd_benchmark(args):
    packager = ResultPackager(lane=args.lane, agent=args.agent or args.lane)
    
    with open(args.metrics_file) as f:
        metrics = json.load(f)
    
    tags = args.tags.split(",") if args.tags else None
    
    artifact = packager.package_benchmark_result(
        test_id=args.test_id,
        title=args.title,
        metrics=metrics,
        task_id=args.task_id,
        thread_id=args.thread_id,
        tags=tags,
    )
    
    print(json.dumps(artifact, indent=2))
    return 0


def cmd_probe(args):
    packager = ResultPackager(lane=args.lane, agent=args.agent or args.lane)
    
    with open(args.findings_file) as f:
        findings = json.load(f)
    
    tags = args.tags.split(",") if args.tags else None
    
    artifact = packager.package_probe_report(
        probe_id=args.probe_id,
        title=args.title,
        findings=findings,
        task_id=args.task_id,
        thread_id=args.thread_id,
        tags=tags,
    )
    
    print(json.dumps(artifact, indent=2))
    return 0


def cmd_evidence(args):
    packager = ResultPackager(lane=args.lane, agent=args.agent or args.lane)
    
    with open(args.evidence_file) as f:
        evidence_data = json.load(f)
    
    tags = args.tags.split(",") if args.tags else None
    
    artifact = packager.package_evidence(
        claim_id=args.claim_id,
        evidence_type=args.evidence_type or "experimental",
        evidence_data=evidence_data,
        stance=args.stance,
        task_id=args.task_id,
        thread_id=args.thread_id,
        tags=tags,
    )
    
    print(json.dumps(artifact, indent=2))
    return 0


def cmd_list(args):
    packager = ResultPackager(lane=args.lane)
    
    artifact_type = None
    if args.type:
        artifact_type = ArtifactType(args.type)
    
    tags = args.tags.split(",") if args.tags else None
    
    artifacts = packager.list_artifacts(artifact_type=artifact_type, tags=tags)
    print(json.dumps(artifacts, indent=2))
    return 0


def cmd_discover(args):
    tags = args.tags.split(",") if args.tags else None
    artifacts = discover_cross_lane_artifacts(target_lane=args.lane, tags=tags)
    print(json.dumps(artifacts, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description="Control Plane Result Packaging CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # benchmark subcommand
    bench_parser = subparsers.add_parser("benchmark", help="Package benchmark results")
    bench_parser.add_argument("--lane", required=True, help="Source lane")
    bench_parser.add_argument("--agent", help="Agent name (defaults to lane)")
    bench_parser.add_argument("--test-id", required=True, help="Test identifier")
    bench_parser.add_argument("--title", required=True, help="Artifact title")
    bench_parser.add_argument("--metrics-file", required=True, help="Path to metrics JSON")
    bench_parser.add_argument("--task-id", help="Associated task ID")
    bench_parser.add_argument("--thread-id", help="Associated thread ID")
    bench_parser.add_argument("--tags", help="Comma-separated tags")
    bench_parser.set_defaults(func=cmd_benchmark)
    
    # probe subcommand
    probe_parser = subparsers.add_parser("probe", help="Package probe reports")
    probe_parser.add_argument("--lane", required=True, help="Source lane")
    probe_parser.add_argument("--agent", help="Agent name (defaults to lane)")
    probe_parser.add_argument("--probe-id", required=True, help="Probe identifier")
    probe_parser.add_argument("--title", required=True, help="Artifact title")
    probe_parser.add_argument("--findings-file", required=True, help="Path to findings JSON")
    probe_parser.add_argument("--task-id", help="Associated task ID")
    probe_parser.add_argument("--thread-id", help="Associated thread ID")
    probe_parser.add_argument("--tags", help="Comma-separated tags")
    probe_parser.set_defaults(func=cmd_probe)
    
    # evidence subcommand
    ev_parser = subparsers.add_parser("evidence", help="Package claim evidence")
    ev_parser.add_argument("--lane", required=True, help="Source lane")
    ev_parser.add_argument("--agent", help="Agent name (defaults to lane)")
    ev_parser.add_argument("--claim-id", required=True, help="Claim identifier")
    ev_parser.add_argument("--stance", required=True, choices=["supports", "weak_support", "contradicts", "invalidates"])
    ev_parser.add_argument("--evidence-type", default="experimental", help="Type of evidence")
    ev_parser.add_argument("--evidence-file", required=True, help="Path to evidence JSON")
    ev_parser.add_argument("--task-id", help="Associated task ID")
    ev_parser.add_argument("--thread-id", help="Associated thread ID")
    ev_parser.add_argument("--tags", help="Comma-separated tags")
    ev_parser.set_defaults(func=cmd_evidence)
    
    # list subcommand
    list_parser = subparsers.add_parser("list", help="List artifacts in a lane")
    list_parser.add_argument("--lane", required=True, help="Lane to list")
    list_parser.add_argument("--type", help="Filter by artifact type")
    list_parser.add_argument("--tags", help="Filter by tags (comma-separated)")
    list_parser.set_defaults(func=cmd_list)
    
    # discover subcommand
    disc_parser = subparsers.add_parser("discover", help="Discover cross-lane artifacts")
    disc_parser.add_argument("--lane", help="Filter to specific lane")
    disc_parser.add_argument("--tags", help="Filter by tags (comma-separated)")
    disc_parser.set_defaults(func=cmd_discover)
    
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
