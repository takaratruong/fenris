"""
Broader-Lane Launcher Runtime Hardening

This module integrates XLA-AOT preflight checks into the broader-lane launcher
entry point. Import and call ensure_runtime_compatible() at the start of any
long-running XLA/JAX workload.

Example usage in launcher:
    from runtime_hardening import ensure_runtime_compatible
    
    def main():
        ensure_runtime_compatible()  # Fast-fail if incompatible
        # ... rest of launcher code
"""

from xla_preflight import preflight_check, PreflightError, PreflightResult
import sys
import os

__all__ = [
    "ensure_runtime_compatible",
    "RuntimeCompatibilityError", 
    "get_runtime_status",
]


class RuntimeCompatibilityError(Exception):
    """
    Raised when runtime environment is incompatible with broader-lane workloads.
    Contains remediation guidance.
    """
    pass


def ensure_runtime_compatible(
    require_gpu: bool = True,
    strict_cpu: bool = False,
    exit_on_failure: bool = True,
    quiet: bool = False,
) -> PreflightResult:
    """
    Validate runtime environment before starting broader-lane workload.
    
    This should be called at the very start of the launcher/entrypoint to
    fast-fail before committing to a long-running computation.
    
    Args:
        require_gpu: Require GPU devices (default True for broader-lane)
        strict_cpu: Fail on missing CPU features (default False since GPU is primary)
        exit_on_failure: Call sys.exit(1) on failure instead of raising
        quiet: Suppress output on success
    
    Returns:
        PreflightResult if successful
    
    Raises:
        RuntimeCompatibilityError: If validation fails (when exit_on_failure=False)
    """
    try:
        result = preflight_check(
            require_gpu=require_gpu,
            strict_cpu=strict_cpu,
            verbose=not quiet,
        )
        
        if not quiet:
            print(f"[runtime-hardening] ✅ Runtime validated: JAX {result.jax_version}, "
                  f"{len(result.jax_devices)} device(s)")
        
        return result
        
    except PreflightError as e:
        error_msg = f"""
================================================================================
BROADER-LANE RUNTIME INCOMPATIBILITY DETECTED
================================================================================

{e.message}

Missing features: {', '.join(e.missing_features) if e.missing_features else 'N/A'}

{e.remediation}

The workload cannot proceed on this host/runtime configuration.
================================================================================
"""
        if exit_on_failure:
            print(error_msg, file=sys.stderr)
            sys.exit(1)
        else:
            raise RuntimeCompatibilityError(error_msg) from e


def get_runtime_status() -> dict:
    """
    Get current runtime status without failing.
    Useful for logging/diagnostics.
    """
    try:
        result = preflight_check(
            require_gpu=False,
            strict_cpu=False,
            verbose=False,
        )
        return {
            "compatible": True,
            "jax_version": result.jax_version,
            "devices": result.jax_devices,
            "cpu_features": result.cpu_features_present,
            "warnings": result.warnings,
        }
    except Exception as e:
        return {
            "compatible": False,
            "error": str(e),
        }


if __name__ == "__main__":
    # Quick test
    print("Testing runtime hardening integration...")
    ensure_runtime_compatible(require_gpu=False, quiet=False)
    print("\nRuntime status:")
    import json
    print(json.dumps(get_runtime_status(), indent=2))
