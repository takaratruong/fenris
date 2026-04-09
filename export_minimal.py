#!/usr/bin/env python3
"""
Export gaussian splat PLY in minimal format (like biker.ply from PlayCanvas)
Strips normals and higher-order SH coefficients for maximum compatibility.
"""

import numpy as np
from plyfile import PlyData, PlyElement
import sys

def load_full_ply(path):
    """Load a full 3DGS PLY file"""
    print(f"Loading: {path}")
    ply = PlyData.read(path)
    return ply['vertex'].data

def export_minimal_ply(data, output_path, max_gaussians=None):
    """Export PLY with minimal properties (no normals, no f_rest)"""
    
    if max_gaussians and len(data) > max_gaussians:
        # Sample by taking highest opacity gaussians
        opacities = 1 / (1 + np.exp(-data['opacity']))
        indices = np.argsort(-opacities)[:max_gaussians]
        data = data[indices]
        print(f"  Sampled {max_gaussians:,} highest-opacity gaussians")
    
    n = len(data)
    print(f"  Exporting {n:,} gaussians")
    
    # Define minimal vertex type (matching biker.ply format)
    dtype = [
        ('x', 'f4'), ('y', 'f4'), ('z', 'f4'),
        ('f_dc_0', 'f4'), ('f_dc_1', 'f4'), ('f_dc_2', 'f4'),
        ('opacity', 'f4'),
        ('scale_0', 'f4'), ('scale_1', 'f4'), ('scale_2', 'f4'),
        ('rot_0', 'f4'), ('rot_1', 'f4'), ('rot_2', 'f4'), ('rot_3', 'f4'),
    ]
    
    minimal = np.empty(n, dtype=dtype)
    
    # Copy position
    minimal['x'] = data['x']
    minimal['y'] = data['y']
    minimal['z'] = data['z']
    
    # Copy SH DC terms (base color)
    minimal['f_dc_0'] = data['f_dc_0']
    minimal['f_dc_1'] = data['f_dc_1']
    minimal['f_dc_2'] = data['f_dc_2']
    
    # Copy opacity
    minimal['opacity'] = data['opacity']
    
    # Copy scales
    minimal['scale_0'] = data['scale_0']
    minimal['scale_1'] = data['scale_1']
    minimal['scale_2'] = data['scale_2']
    
    # Copy and normalize quaternions
    rot_0 = data['rot_0']
    rot_1 = data['rot_1']
    rot_2 = data['rot_2']
    rot_3 = data['rot_3']
    norm = np.sqrt(rot_0**2 + rot_1**2 + rot_2**2 + rot_3**2)
    minimal['rot_0'] = rot_0 / norm
    minimal['rot_1'] = rot_1 / norm
    minimal['rot_2'] = rot_2 / norm
    minimal['rot_3'] = rot_3 / norm
    
    # Create PLY
    el = PlyElement.describe(minimal, 'vertex')
    PlyData([el], text=False).write(output_path)
    
    import os
    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"  Wrote {output_path} ({size_mb:.1f} MB)")


def main():
    input_path = "/home/ubuntu/datasets/sage_batch_walking/0001_839920_seq_006_fruit/gs_output_15k/point_cloud/iteration_15000/point_cloud.ply"
    
    data = load_full_ply(input_path)
    print(f"Total gaussians: {len(data):,}")
    
    # Export full minimal (no f_rest, no normals)
    export_minimal_ply(data, "/home/ubuntu/.openclaw/workspace/minimal_full.ply")
    
    # Export small sample that fits Discord limit (<8MB)
    # 14 floats * 4 bytes * N < 8MB -> N < 143k
    # But with PLY overhead, aim for ~100k
    export_minimal_ply(data, "/home/ubuntu/.openclaw/workspace/minimal_100k.ply", max_gaussians=100000)
    
    # Even smaller for quick test
    export_minimal_ply(data, "/home/ubuntu/.openclaw/workspace/minimal_30k.ply", max_gaussians=30000)


if __name__ == "__main__":
    main()
