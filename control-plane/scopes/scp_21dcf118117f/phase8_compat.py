#!/usr/bin/env python3
"""
Phase8 Verifier Compatibility Shell
Task: tsk_9f7a91aeae32
Thread: thr_462509adf115

Provides backward-compatible interface for phase8 verification workflows
within the scheduler-backed scope system.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any

CONTROL_PLANE_DB = "/home/ubuntu/.openclaw/workspace/control-plane/control_plane.db"
SCOPE_DIR = Path("/home/ubuntu/.openclaw/workspace/control-plane/scopes/scp_21dcf118117f")


class Phase8CompatShell:
    """
    Compatibility shell for phase8 verification workflows.
    
    Provides:
    - Legacy phase8 verifier API compatibility
    - Bridge to scheduler-backed scope system
    - Verification result normalization
    - Heartbeat/lease management shims
    """
    
    VERSION = "1.0.0"
    COMPAT_LEVEL = "phase8"
    
    def __init__(self, task_id: str = None, thread_id: str = None):
        self.task_id = task_id or "tsk_9f7a91aeae32"
        self.thread_id = thread_id or "thr_462509adf115"
        self.db_path = CONTROL_PLANE_DB
        self._init_time = datetime.utcnow()
        
    def verify_claim(self, claim_id: str = None) -> Dict[str, Any]:
        """
        Verify a task claim is valid (phase8-compatible interface).
        
        In the new scheduler model, claims are tracked in the claims table.
        This method provides backward compatibility.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            """SELECT c.id, c.task_id, c.agent_id, c.status, c.last_heartbeat
               FROM claims c
               WHERE c.task_id = ? AND c.status = 'active'
               ORDER BY c.claimed_at DESC LIMIT 1""",
            (self.task_id,)
        )
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                "valid": True,
                "claim_id": row[0],
                "task_id": row[1],
                "agent_id": row[2],
                "status": row[3],
                "last_heartbeat": row[4],
                "compat_mode": "phase8"
            }
        
        # No active claim - return compat-mode result
        return {
            "valid": False,
            "claim_id": claim_id,
            "task_id": self.task_id,
            "reason": "no_active_claim",
            "compat_mode": "phase8",
            "suggestion": "create_new_claim"
        }
    
    def ensure_lease(self, lease_duration_ms: int = 300000) -> Dict[str, Any]:
        """
        Ensure a valid lease exists for the current task.
        Creates one if missing (phase8 compat behavior).
        """
        claim_check = self.verify_claim()
        
        if claim_check["valid"]:
            return {
                "success": True,
                "lease_type": "existing_claim",
                "expires_at": self._calculate_expiry(lease_duration_ms),
                "claim_id": claim_check["claim_id"]
            }
        
        # Create a shim claim for phase8 compat
        claim_id = f"clm_phase8_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO claims (id, task_id, agent_id, session_id, status)
               VALUES (?, ?, 'engineer', 'phase8_compat', 'active')""",
            (claim_id, self.task_id)
        )
        conn.commit()
        conn.close()
        
        return {
            "success": True,
            "lease_type": "new_compat_claim",
            "claim_id": claim_id,
            "expires_at": self._calculate_expiry(lease_duration_ms),
            "compat_mode": "phase8"
        }
    
    def _calculate_expiry(self, duration_ms: int) -> str:
        """Calculate lease expiry timestamp."""
        from datetime import timedelta
        expiry = datetime.utcnow() + timedelta(milliseconds=duration_ms)
        return expiry.isoformat() + "Z"
    
    def heartbeat(self) -> Dict[str, Any]:
        """
        Send heartbeat for the current task's claim.
        Phase8-compatible interface.
        """
        conn = sqlite3.connect(self.db_path)
        now = datetime.utcnow().isoformat()
        
        cursor = conn.execute(
            """UPDATE claims 
               SET last_heartbeat = ?
               WHERE task_id = ? AND status = 'active'""",
            (now, self.task_id)
        )
        updated = cursor.rowcount
        conn.commit()
        conn.close()
        
        return {
            "success": updated > 0,
            "heartbeat_at": now,
            "task_id": self.task_id,
            "compat_mode": "phase8"
        }
    
    def post_progress(self, message: str, progress_pct: int = None) -> Dict[str, Any]:
        """
        Post progress update (phase8 interface).
        Maps to task_updates table.
        """
        conn = sqlite3.connect(self.db_path)
        
        content = {"message": message}
        if progress_pct is not None:
            content["progress_pct"] = progress_pct
        
        conn.execute(
            """INSERT INTO task_updates (task_id, agent_id, update_type, content)
               VALUES (?, 'engineer', 'progress', ?)""",
            (self.task_id, json.dumps(content))
        )
        conn.commit()
        conn.close()
        
        return {
            "success": True,
            "task_id": self.task_id,
            "update_type": "progress",
            "compat_mode": "phase8"
        }
    
    def transition(self, new_status: str, result: Dict = None) -> Dict[str, Any]:
        """
        Transition task to new status (phase8 interface).
        Valid statuses: pending, running, completed, failed, blocked
        """
        valid_statuses = ["pending", "assigned", "running", "completed", "done", "failed", "blocked"]
        if new_status not in valid_statuses:
            return {
                "success": False,
                "error": f"Invalid status: {new_status}",
                "valid_statuses": valid_statuses
            }
        
        conn = sqlite3.connect(self.db_path)
        now = datetime.utcnow().isoformat()
        
        # Update task status
        if new_status in ("completed", "done", "failed"):
            conn.execute(
                """UPDATE tasks 
                   SET status = ?, updated_at = ?, completed_at = ?
                   WHERE id = ?""",
                (new_status, now, now, self.task_id)
            )
            # Release claim
            conn.execute(
                """UPDATE claims SET status = 'released' WHERE task_id = ? AND status = 'active'""",
                (self.task_id,)
            )
        else:
            conn.execute(
                """UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?""",
                (new_status, now, self.task_id)
            )
        
        # Log the transition
        conn.execute(
            """INSERT INTO task_updates (task_id, agent_id, update_type, content)
               VALUES (?, 'engineer', 'transition', ?)""",
            (self.task_id, json.dumps({"new_status": new_status, "result": result}))
        )
        
        conn.commit()
        conn.close()
        
        return {
            "success": True,
            "task_id": self.task_id,
            "new_status": new_status,
            "transitioned_at": now,
            "compat_mode": "phase8"
        }
    
    def get_status(self) -> Dict[str, Any]:
        """Get current task and compat shell status."""
        conn = sqlite3.connect(self.db_path)
        
        cursor = conn.execute(
            "SELECT id, title, status, assigned_to, updated_at FROM tasks WHERE id = ?",
            (self.task_id,)
        )
        task_row = cursor.fetchone()
        
        cursor = conn.execute(
            "SELECT id, status, last_heartbeat FROM claims WHERE task_id = ? AND status = 'active'",
            (self.task_id,)
        )
        claim_row = cursor.fetchone()
        conn.close()
        
        return {
            "compat_shell_version": self.VERSION,
            "compat_level": self.COMPAT_LEVEL,
            "task": {
                "id": task_row[0] if task_row else None,
                "title": task_row[1] if task_row else None,
                "status": task_row[2] if task_row else None,
                "assigned_to": task_row[3] if task_row else None,
                "updated_at": task_row[4] if task_row else None
            } if task_row else None,
            "claim": {
                "id": claim_row[0],
                "status": claim_row[1],
                "last_heartbeat": claim_row[2]
            } if claim_row else None,
            "uptime_seconds": (datetime.utcnow() - self._init_time).total_seconds()
        }


def run_compat_verification() -> Dict[str, Any]:
    """
    Run verification of the compat shell itself.
    Ensures phase8 interface works correctly.
    """
    results = {
        "shell_version": Phase8CompatShell.VERSION,
        "compat_level": Phase8CompatShell.COMPAT_LEVEL,
        "verified_at": datetime.utcnow().isoformat(),
        "checks": []
    }
    
    shell = Phase8CompatShell()
    
    # Check 1: Shell instantiation
    try:
        status = shell.get_status()
        results["checks"].append({
            "name": "shell_instantiation",
            "passed": True,
            "details": f"Task status: {status['task']['status'] if status['task'] else 'N/A'}"
        })
    except Exception as e:
        results["checks"].append({
            "name": "shell_instantiation",
            "passed": False,
            "details": str(e)
        })
    
    # Check 2: Claim verification
    try:
        claim_result = shell.verify_claim()
        results["checks"].append({
            "name": "claim_verification",
            "passed": True,
            "details": f"Valid: {claim_result['valid']}, Mode: {claim_result['compat_mode']}"
        })
    except Exception as e:
        results["checks"].append({
            "name": "claim_verification",
            "passed": False,
            "details": str(e)
        })
    
    # Check 3: Lease management
    try:
        lease_result = shell.ensure_lease()
        results["checks"].append({
            "name": "lease_management",
            "passed": lease_result["success"],
            "details": f"Type: {lease_result['lease_type']}"
        })
    except Exception as e:
        results["checks"].append({
            "name": "lease_management",
            "passed": False,
            "details": str(e)
        })
    
    # Check 4: Heartbeat
    try:
        hb_result = shell.heartbeat()
        results["checks"].append({
            "name": "heartbeat",
            "passed": hb_result["success"],
            "details": f"At: {hb_result['heartbeat_at']}"
        })
    except Exception as e:
        results["checks"].append({
            "name": "heartbeat",
            "passed": False,
            "details": str(e)
        })
    
    # Check 5: Progress posting
    try:
        progress_result = shell.post_progress("Compat shell verification in progress", 50)
        results["checks"].append({
            "name": "progress_posting",
            "passed": progress_result["success"],
            "details": "Progress update recorded"
        })
    except Exception as e:
        results["checks"].append({
            "name": "progress_posting",
            "passed": False,
            "details": str(e)
        })
    
    # Summary
    passed = sum(1 for c in results["checks"] if c["passed"])
    total = len(results["checks"])
    results["summary"] = {
        "passed": passed,
        "total": total,
        "success": passed == total
    }
    
    return results


if __name__ == "__main__":
    print("Phase8 Verifier Compatibility Shell")
    print("=" * 50)
    
    results = run_compat_verification()
    
    for check in results["checks"]:
        status = "✓" if check["passed"] else "✗"
        print(f"{status} {check['name']}: {check['details']}")
    
    print("=" * 50)
    print(f"Summary: {results['summary']['passed']}/{results['summary']['total']} checks passed")
    
    # Save results
    output_path = SCOPE_DIR / "phase8_compat_verification.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {output_path}")
