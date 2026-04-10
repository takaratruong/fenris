#!/usr/bin/env python3
"""Unit tests for result_packaging module."""

import json
import tempfile
import shutil
from pathlib import Path
import sys

# Test in isolation with temp directory
def test_result_packaging():
    # Import the module
    sys.path.insert(0, str(Path(__file__).parent))
    from result_packaging import ResultPackager, ArtifactType, Visibility, discover_cross_lane_artifacts
    
    print("Testing Result Packaging Flow...")
    
    # Test 1: Package benchmark result
    packager = ResultPackager(lane="bench", agent="bench")
    
    # Verify the bench lane got an artifact
    artifacts = packager.list_artifacts()
    assert len(artifacts) >= 1, "Should have at least one artifact"
    print(f"✓ Found {len(artifacts)} artifact(s) in bench lane")
    
    # Test 2: Get specific artifact
    artifact = packager.get_artifact("art_bn_001_fenris_stress_test_3")
    assert artifact is not None, "Should find the stress test artifact"
    assert artifact["artifact_type"] == "benchmark_result"
    print("✓ Retrieved benchmark artifact successfully")
    
    # Test 3: Cross-lane discovery
    all_artifacts = discover_cross_lane_artifacts()
    assert len(all_artifacts) >= 2, "Should find artifacts from multiple lanes"
    
    lanes_found = set(a["_source_lane"] for a in all_artifacts)
    print(f"✓ Cross-lane discovery found artifacts from: {lanes_found}")
    
    # Test 4: Tag filtering
    fenris_artifacts = discover_cross_lane_artifacts(tags=["fenris"])
    assert len(fenris_artifacts) >= 1, "Should find fenris-tagged artifacts"
    print(f"✓ Tag filtering works: found {len(fenris_artifacts)} fenris artifact(s)")
    
    # Test 5: Lane-specific discovery
    research_only = discover_cross_lane_artifacts(target_lane="research")
    assert all(a["_source_lane"] == "research" for a in research_only)
    print(f"✓ Lane filter works: found {len(research_only)} research artifact(s)")
    
    print("\n✅ All tests passed!")
    return 0


if __name__ == "__main__":
    sys.exit(test_result_packaging())
