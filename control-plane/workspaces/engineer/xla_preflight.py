#!/usr/bin/env python3
"""
XLA-AOT CPU-Feature Preflight Check for Broader-Lane Launcher

This module provides fast-fail validation before long-running XLA/JAX workloads.
It checks for CPU feature compatibility with AOT-compiled XLA kernels and provides
explicit remediation guidance when issues are detected.

Usage:
    from xla_preflight import preflight_check
    preflight_check()  # Raises PreflightError with remediation if issues found

    # Or run as script for quick validation:
    python xla_preflight.py
"""

import os
import sys
import subprocess
from dataclasses import dataclass
from typing import Optional, List, Tuple

__version__ = "1.0.0"

# CPU features required for optimal XLA AOT execution
# These are commonly required by JAX/XLA compiled kernels
REQUIRED_CPU_FEATURES = {
    "avx": "Advanced Vector Extensions - required for vectorized ops",
    "avx2": "AVX2 - required for modern XLA kernels",
    "fma": "Fused Multiply-Add - required for efficient matmul",
    "sse4_1": "SSE4.1 - baseline SIMD requirement",
    "sse4_2": "SSE4.2 - baseline SIMD requirement",
}

# Optional but recommended features
RECOMMENDED_CPU_FEATURES = {
    "avx512f": "AVX-512 Foundation - enables wider vector ops",
    "avx512vl": "AVX-512 Vector Length - optimizes mixed-width ops",
}

# XLA environment variables that affect AOT behavior
XLA_CRITICAL_ENV_VARS = [
    "XLA_FLAGS",
    "XLA_PYTHON_CLIENT_PREALLOCATE",
    "XLA_PYTHON_CLIENT_MEM_FRACTION",
    "XLA_PYTHON_CLIENT_ALLOCATOR",
    "JAX_PLATFORMS",
    "JAX_ENABLE_X64",
    "CUDA_VISIBLE_DEVICES",
]


class PreflightError(Exception):
    """Raised when preflight checks fail with remediation guidance."""
    
    def __init__(self, message: str, remediation: str, missing_features: Optional[List[str]] = None):
        self.message = message
        self.remediation = remediation
        self.missing_features = missing_features or []
        super().__init__(self._format_error())
    
    def _format_error(self) -> str:
        lines = [
            "",
            "=" * 70,
            "XLA-AOT PREFLIGHT CHECK FAILED",
            "=" * 70,
            "",
            f"Error: {self.message}",
            "",
        ]
        if self.missing_features:
            lines.append("Missing CPU features:")
            for feat in self.missing_features:
                desc = REQUIRED_CPU_FEATURES.get(feat, "")
                lines.append(f"  - {feat}: {desc}")
            lines.append("")
        lines.extend([
            "Remediation:",
            self.remediation,
            "",
            "=" * 70,
        ])
        return "\n".join(lines)


@dataclass
class PreflightResult:
    """Result of preflight validation."""
    passed: bool
    cpu_features_present: List[str]
    cpu_features_missing: List[str]
    cpu_features_recommended_missing: List[str]
    xla_env_vars: dict
    jax_version: Optional[str]
    jax_devices: List[str]
    warnings: List[str]
    
    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines = [
            f"Preflight Status: {status}",
            f"JAX Version: {self.jax_version or 'not installed'}",
            f"Devices: {', '.join(self.jax_devices) or 'none'}",
            f"CPU Features Present: {', '.join(self.cpu_features_present) or 'none detected'}",
        ]
        if self.cpu_features_missing:
            lines.append(f"CPU Features Missing (REQUIRED): {', '.join(self.cpu_features_missing)}")
        if self.cpu_features_recommended_missing:
            lines.append(f"CPU Features Missing (recommended): {', '.join(self.cpu_features_recommended_missing)}")
        if self.warnings:
            lines.append("Warnings:")
            for w in self.warnings:
                lines.append(f"  - {w}")
        return "\n".join(lines)


def get_cpu_features() -> Tuple[set, str]:
    """
    Detect CPU features from /proc/cpuinfo (Linux) or sysctl (macOS).
    Returns (set of features, raw cpu info string).
    """
    features = set()
    raw_info = ""
    
    if sys.platform == "linux":
        try:
            with open("/proc/cpuinfo", "r") as f:
                raw_info = f.read()
            for line in raw_info.split("\n"):
                if line.startswith("flags"):
                    # flags line: "flags : fpu vme de pse ..."
                    parts = line.split(":")
                    if len(parts) >= 2:
                        features = set(parts[1].strip().split())
                    break
        except (IOError, OSError) as e:
            raw_info = f"Error reading /proc/cpuinfo: {e}"
    
    elif sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-a"],
                capture_output=True,
                text=True,
                timeout=5
            )
            raw_info = result.stdout
            # macOS uses different naming
            feature_map = {
                "hw.optional.avx1_0": "avx",
                "hw.optional.avx2_0": "avx2",
                "hw.optional.fma": "fma",
                "hw.optional.sse4_1": "sse4_1",
                "hw.optional.sse4_2": "sse4_2",
                "hw.optional.avx512f": "avx512f",
                "hw.optional.avx512vl": "avx512vl",
            }
            for line in raw_info.split("\n"):
                for sysctl_key, feature_name in feature_map.items():
                    if line.startswith(sysctl_key) and ": 1" in line:
                        features.add(feature_name)
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            raw_info = f"Error running sysctl: {e}"
    
    return features, raw_info


def get_xla_env_vars() -> dict:
    """Collect XLA-related environment variables."""
    return {
        var: os.environ.get(var, "<not set>")
        for var in XLA_CRITICAL_ENV_VARS
    }


def check_jax_availability() -> Tuple[Optional[str], List[str], List[str]]:
    """
    Check JAX installation and device availability.
    Returns (version, device_list, warnings).
    """
    warnings = []
    try:
        import jax
        version = jax.__version__
        
        try:
            devices = [str(d) for d in jax.devices()]
        except Exception as e:
            devices = []
            warnings.append(f"Could not enumerate JAX devices: {e}")
        
        return version, devices, warnings
    
    except ImportError:
        return None, [], ["JAX not installed in current environment"]


def validate_cpu_features(features: set) -> Tuple[List[str], List[str], List[str]]:
    """
    Validate CPU features against requirements.
    Returns (present, missing_required, missing_recommended).
    """
    present = []
    missing_required = []
    missing_recommended = []
    
    for feat in REQUIRED_CPU_FEATURES:
        if feat in features:
            present.append(feat)
        else:
            missing_required.append(feat)
    
    for feat in RECOMMENDED_CPU_FEATURES:
        if feat in features:
            present.append(feat)
        else:
            missing_recommended.append(feat)
    
    return present, missing_required, missing_recommended


def preflight_check(
    require_gpu: bool = False,
    strict_cpu: bool = True,
    verbose: bool = False
) -> PreflightResult:
    """
    Run XLA-AOT preflight validation.
    
    Args:
        require_gpu: If True, fail if no GPU devices available
        strict_cpu: If True, fail on missing required CPU features
        verbose: If True, print detailed output
    
    Returns:
        PreflightResult with validation details
    
    Raises:
        PreflightError: If validation fails with remediation guidance
    """
    warnings = []
    
    # 1. Check CPU features
    cpu_features, raw_cpu_info = get_cpu_features()
    present, missing_required, missing_recommended = validate_cpu_features(cpu_features)
    
    if verbose:
        print(f"Detected CPU features: {len(cpu_features)} total")
        print(f"Required features present: {present}")
        print(f"Required features missing: {missing_required}")
    
    # 2. Check XLA environment
    xla_env = get_xla_env_vars()
    
    # 3. Check JAX
    jax_version, jax_devices, jax_warnings = check_jax_availability()
    warnings.extend(jax_warnings)
    
    # 4. Determine pass/fail
    passed = True
    
    if strict_cpu and missing_required:
        passed = False
    
    if require_gpu:
        gpu_devices = [d for d in jax_devices if "cuda" in d.lower() or "gpu" in d.lower()]
        if not gpu_devices:
            passed = False
            warnings.append("No GPU devices detected but require_gpu=True")
    
    if missing_recommended:
        warnings.append(
            f"Recommended CPU features missing: {', '.join(missing_recommended)}. "
            "Performance may be suboptimal."
        )
    
    result = PreflightResult(
        passed=passed,
        cpu_features_present=present,
        cpu_features_missing=missing_required,
        cpu_features_recommended_missing=missing_recommended,
        xla_env_vars=xla_env,
        jax_version=jax_version,
        jax_devices=jax_devices,
        warnings=warnings,
    )
    
    # Raise error with remediation if failed
    if not passed:
        if missing_required:
            remediation = """
Host CPU lacks required features for XLA AOT kernels. Options:

1. Run on compatible hardware:
   - Ensure host CPU supports AVX2/FMA (Intel Haswell+ or AMD Zen+)
   - Check virtualization settings if running in VM (enable AVX passthrough)

2. Disable AOT and use JIT compilation:
   export XLA_FLAGS="--xla_cpu_enable_fast_math=false"
   export JAX_PLATFORMS="cpu"

3. Force CPU feature emulation (slower, for testing only):
   export XLA_FLAGS="--xla_cpu_multi_thread_eigen=false"

4. Use GPU backend to bypass CPU feature requirements:
   export JAX_PLATFORMS="cuda"
   export CUDA_VISIBLE_DEVICES=0
"""
        elif require_gpu and not jax_devices:
            remediation = """
No GPU devices detected. Options:

1. Verify CUDA installation:
   nvidia-smi
   python -c "import jax; print(jax.devices())"

2. Set visible devices explicitly:
   export CUDA_VISIBLE_DEVICES=0,1,2,3

3. Fall back to CPU (slower):
   export JAX_PLATFORMS="cpu"
"""
        else:
            remediation = "Check warnings above for specific issues."
        
        raise PreflightError(
            message="Preflight validation failed",
            remediation=remediation,
            missing_features=missing_required,
        )
    
    return result


def main():
    """CLI entry point for preflight check."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="XLA-AOT CPU-feature preflight check for broader-lane launcher"
    )
    parser.add_argument(
        "--require-gpu", "-g",
        action="store_true",
        help="Require GPU devices to be available"
    )
    parser.add_argument(
        "--no-strict-cpu",
        action="store_true",
        help="Don't fail on missing CPU features (warn only)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON"
    )
    
    args = parser.parse_args()
    
    try:
        result = preflight_check(
            require_gpu=args.require_gpu,
            strict_cpu=not args.no_strict_cpu,
            verbose=args.verbose,
        )
        
        if args.json:
            import json
            print(json.dumps({
                "passed": result.passed,
                "cpu_features_present": result.cpu_features_present,
                "cpu_features_missing": result.cpu_features_missing,
                "cpu_features_recommended_missing": result.cpu_features_recommended_missing,
                "xla_env_vars": result.xla_env_vars,
                "jax_version": result.jax_version,
                "jax_devices": result.jax_devices,
                "warnings": result.warnings,
            }, indent=2))
        else:
            print(result.summary())
            print("\n✅ Preflight check PASSED")
        
        sys.exit(0)
        
    except PreflightError as e:
        if args.json:
            import json
            print(json.dumps({
                "passed": False,
                "error": e.message,
                "missing_features": e.missing_features,
                "remediation": e.remediation,
            }, indent=2))
        else:
            print(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
