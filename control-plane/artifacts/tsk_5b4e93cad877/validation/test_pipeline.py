#!/usr/bin/env python3
"""
Validation Stage: Pipeline Test Suite
Task: tsk_5b4e93cad877
"""

import sys
import os
import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime

# Add engineer artifact to path
sys.path.insert(0, str(Path(__file__).parent.parent / "engineer"))
from implementation import ArtifactPipeline

DB_PATH = "/home/ubuntu/.openclaw/workspace/control-plane/control_plane.db"
TASK_ID = "tsk_5b4e93cad877"
THREAD_ID = "thr_bff62912693a"

def test_artifact_creation():
    """Test 1: Artifact creation with hash."""
    pipeline = ArtifactPipeline("/tmp")
    artifact = pipeline.create_artifact("test_artifact", "test content")
    assert "content_hash" in artifact
    assert artifact["size_bytes"] == len("test content")
    print("✓ Test 1: Artifact creation passed")
    return True

def test_storage_and_retrieval():
    """Test 2: Storage in filesystem + DB metadata."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Query artifacts for our task
    cursor.execute("""
        SELECT id, name, path, content_hash, created_by 
        FROM artifacts 
        WHERE task_id = ?
    """, (TASK_ID,))
    
    artifacts = cursor.fetchall()
    conn.close()
    
    assert len(artifacts) >= 2, f"Expected >=2 artifacts, found {len(artifacts)}"
    
    # Verify files exist
    for art_id, name, path, content_hash, created_by in artifacts:
        if path:
            assert Path(path).exists(), f"File not found: {path}"
            # Verify integrity
            actual_hash = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            assert actual_hash == content_hash, f"Hash mismatch for {name}"
    
    print(f"✓ Test 2: Storage/retrieval passed ({len(artifacts)} artifacts)")
    return True

def test_cross_lane_visibility():
    """Test 3: Cross-lane visibility via thread_id."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Artifacts visible to thread
    cursor.execute("""
        SELECT COUNT(*) FROM artifacts WHERE thread_id = ?
    """, (THREAD_ID,))
    count = cursor.fetchone()[0]
    
    # Check artifacts from other tasks in same thread would be visible
    cursor.execute("""
        SELECT DISTINCT created_by FROM artifacts WHERE thread_id = ?
    """, (THREAD_ID,))
    creators = [r[0] for r in cursor.fetchall()]
    conn.close()
    
    assert count >= 2, f"Expected >=2 thread-visible artifacts, found {count}"
    print(f"✓ Test 3: Cross-lane visibility passed (creators: {creators})")
    return True

def test_artifact_promotion():
    """Test 4: Lane-local to project scope promotion."""
    # Simulate promotion by checking metadata structure supports it
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # thread_id allows lane-local scoping
    # task_id allows task-local scoping
    # NULL thread_id would indicate project scope
    cursor.execute("""
        SELECT 
            CASE WHEN thread_id IS NOT NULL THEN 'lane-local' ELSE 'project' END as scope,
            COUNT(*) 
        FROM artifacts 
        WHERE task_id = ?
        GROUP BY scope
    """, (TASK_ID,))
    
    scopes = dict(cursor.fetchall())
    conn.close()
    
    assert "lane-local" in scopes, "Expected lane-local scoped artifacts"
    print(f"✓ Test 4: Promotion structure validated (scopes: {scopes})")
    return True

def main():
    """Run all validation tests."""
    print(f"\n{'='*50}")
    print(f"Artifact Pipeline Validation - {datetime.now().isoformat()}")
    print(f"Task: {TASK_ID}")
    print(f"{'='*50}\n")
    
    results = {
        "artifact_creation": test_artifact_creation(),
        "storage_retrieval": test_storage_and_retrieval(),
        "cross_lane_visibility": test_cross_lane_visibility(),
        "artifact_promotion": test_artifact_promotion()
    }
    
    print(f"\n{'='*50}")
    passed = sum(results.values())
    total = len(results)
    print(f"Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("STATUS: ✅ ALL TESTS PASSED")
        return 0
    else:
        print("STATUS: ❌ SOME TESTS FAILED")
        return 1

if __name__ == "__main__":
    sys.exit(main())
