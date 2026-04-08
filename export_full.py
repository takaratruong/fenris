#!/usr/bin/env python3
"""
Properly export gaussian splat PLY with ALL spherical harmonics intact
"""

import numpy as np
from plyfile import PlyData, PlyElement
import json
from pathlib import Path


def load_and_transform(ply_path, labels_path, translation, rotation_z_deg, subsample=None):
    """Load PLY, transform one object, optionally subsample"""
    
    print(f"Loading {ply_path}...")
    ply = PlyData.read(ply_path)
    v = ply['vertex'].data
    n = len(v)
    print(f"  {n:,} gaussians")
    
    # Get all property names
    props = list(v.dtype.names)
    print(f"  {len(props)} properties")
    
    # Extract positions and quaternions for transformation
    xyz = np.stack([v['x'].copy(), v['y'].copy(), v['z'].copy()], axis=1).astype(np.float32)
    quats = np.stack([v['rot_0'].copy(), v['rot_1'].copy(), v['rot_2'].copy(), v['rot_3'].copy()], axis=1).astype(np.float32)
    
    # Segment objects
    print(f"\nSegmenting from {labels_path}...")
    with open(labels_path) as f:
        labels = json.load(f)
    
    objects = []
    margin = 0.05
    for item in labels:
        if 'bounding_box' not in item:
            continue
        pts = np.array([[c['x'], c['y'], c['z']] for c in item['bounding_box']])
        bbox_min, bbox_max = pts.min(axis=0), pts.max(axis=0)
        
        mask = np.all((xyz >= bbox_min - margin) & (xyz <= bbox_max + margin), axis=1)
        indices = np.where(mask)[0]
        
        if len(indices) > 100:
            objects.append({'label': item['label'], 'indices': indices})
    
    # Pick target
    target = None
    for label in ['chair', 'Multi person sofa', 'table']:
        candidates = [o for o in objects if o['label'] == label and len(o['indices']) > 5000]
        if candidates:
            target = max(candidates, key=lambda o: len(o['indices']))
            break
    
    if not target:
        target = max(objects, key=lambda o: len(o['indices']))
    
    print(f"  Selected: {target['label']} ({len(target['indices']):,} gaussians)")
    indices = target['indices']
    
    # Transform
    print(f"\nTransforming: translate={translation}, rotate={rotation_z_deg}°")
    pivot = xyz[indices].mean(axis=0)
    
    if rotation_z_deg != 0:
        theta = np.radians(rotation_z_deg)
        c, s = np.cos(theta), np.sin(theta)
        R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float32)
        
        centered = xyz[indices] - pivot
        xyz[indices] = (R @ centered.T).T + pivot
        
        # Rotate quaternions
        R_quat = np.array([np.cos(theta/2), 0, 0, np.sin(theta/2)], dtype=np.float32)
        for i in indices:
            q = quats[i]
            w1, x1, y1, z1 = R_quat
            w2, x2, y2, z2 = q
            quats[i] = np.array([
                w1*w2 - x1*x2 - y1*y2 - z1*z2,
                w1*x2 + x1*w2 + y1*z2 - z1*y2,
                w1*y2 - x1*z2 + y1*w2 + z1*x2,
                w1*z2 + x1*y2 - y1*x2 + z1*w2
            ], dtype=np.float32)
            quats[i] /= np.linalg.norm(quats[i])
    
    xyz[indices] += np.array(translation, dtype=np.float32)
    
    # Subsample if requested
    if subsample and n > subsample:
        # Keep all target points, sample the rest
        other_idx = np.setdiff1d(np.arange(n), indices)
        n_other = min(subsample - len(indices), len(other_idx))
        other_sample = np.random.choice(other_idx, n_other, replace=False)
        keep_idx = np.sort(np.concatenate([other_sample, indices]))
        print(f"\nSubsampled to {len(keep_idx):,} gaussians")
    else:
        keep_idx = np.arange(n)
    
    return v, xyz, quats, keep_idx, props, target['label']


def write_full_ply(v, xyz, quats, keep_idx, props, output_path):
    """Write PLY preserving ALL properties"""
    
    n = len(keep_idx)
    
    # Build dtype from original properties
    dtype = [(p, 'f4') for p in props]
    
    vertices = np.zeros(n, dtype=dtype)
    
    # Copy all properties
    for p in props:
        if p == 'x':
            vertices['x'] = xyz[keep_idx, 0]
        elif p == 'y':
            vertices['y'] = xyz[keep_idx, 1]
        elif p == 'z':
            vertices['z'] = xyz[keep_idx, 2]
        elif p == 'rot_0':
            vertices['rot_0'] = quats[keep_idx, 0]
        elif p == 'rot_1':
            vertices['rot_1'] = quats[keep_idx, 1]
        elif p == 'rot_2':
            vertices['rot_2'] = quats[keep_idx, 2]
        elif p == 'rot_3':
            vertices['rot_3'] = quats[keep_idx, 3]
        else:
            vertices[p] = v[p][keep_idx]
    
    el = PlyElement.describe(vertices, 'vertex')
    PlyData([el]).write(output_path)
    
    size_mb = Path(output_path).stat().st_size / 1024 / 1024
    print(f"  Wrote {output_path} ({n:,} gaussians, {size_mb:.1f} MB)")


def main():
    ply_path = "/home/ubuntu/datasets/sage_batch_walking/0001_839920_seq_006_fruit/gs_output_15k/point_cloud/iteration_15000/point_cloud.ply"
    labels_path = "/home/ubuntu/datasets/scenes/0001_839920/labels.json"
    output_dir = Path("/home/ubuntu/.openclaw/workspace")
    
    print("=" * 60)
    print("Full PLY Export (with all SH coefficients)")
    print("=" * 60)
    
    # BEFORE - just copy original with subsample
    print("\n--- BEFORE (original) ---")
    ply = PlyData.read(ply_path)
    v = ply['vertex'].data
    props = list(v.dtype.names)
    n = len(v)
    
    # Subsample for manageable file size
    subsample = 150000
    if n > subsample:
        idx = np.random.choice(n, subsample, replace=False)
        idx = np.sort(idx)
    else:
        idx = np.arange(n)
    
    xyz = np.stack([v['x'], v['y'], v['z']], axis=1).astype(np.float32)
    quats = np.stack([v['rot_0'], v['rot_1'], v['rot_2'], v['rot_3']], axis=1).astype(np.float32)
    
    write_full_ply(v, xyz, quats, idx, props, output_dir / "full_before.ply")
    
    # AFTER - with transformation
    print("\n--- AFTER (transformed) ---")
    v, xyz_new, quats_new, keep_idx, props, label = load_and_transform(
        ply_path, labels_path,
        translation=[1.5, 1.0, 0.3],
        rotation_z_deg=45,
        subsample=150000
    )
    
    write_full_ply(v, xyz_new, quats_new, keep_idx, props, output_dir / "full_after.ply")
    
    print("\n" + "=" * 60)
    print("DONE!")
    print("=" * 60)
    print(f"\nFiles:")
    print(f"  {output_dir / 'full_before.ply'}")
    print(f"  {output_dir / 'full_after.ply'}")
    print(f"\nView in SuperSplat: https://playcanvas.com/supersplat/editor")


if __name__ == "__main__":
    main()
