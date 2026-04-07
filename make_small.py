#!/usr/bin/env python3
"""
Create smaller subsampled PLY files for easy sharing
"""

import numpy as np
from plyfile import PlyData, PlyElement
import json
from pathlib import Path


def load_splat(ply_path):
    """Load gaussian splat PLY"""
    ply = PlyData.read(ply_path)
    v = ply['vertex'].data
    return v, len(v['x'])


def segment_objects(xyz, labels_json, margin=0.05):
    """Segment by bounding boxes"""
    with open(labels_json) as f:
        labels = json.load(f)
    
    objects = []
    for item in labels:
        if 'bounding_box' not in item:
            continue
        pts = np.array([[c['x'], c['y'], c['z']] for c in item['bounding_box']])
        bbox_min, bbox_max = pts.min(axis=0), pts.max(axis=0)
        
        mask = np.all((xyz >= bbox_min - margin) & (xyz <= bbox_max + margin), axis=1)
        indices = np.where(mask)[0]
        
        if len(indices) > 100:
            objects.append({'label': item['label'], 'indices': indices})
    
    return objects


def transform_indices(v, indices, translation, rotation_z_deg):
    """Transform object in place"""
    xyz = np.stack([v['x'].copy(), v['y'].copy(), v['z'].copy()], axis=1)
    quats = np.stack([v['rot_0'].copy(), v['rot_1'].copy(), v['rot_2'].copy(), v['rot_3'].copy()], axis=1)
    
    pivot = xyz[indices].mean(axis=0)
    
    if rotation_z_deg != 0:
        theta = np.radians(rotation_z_deg)
        c, s = np.cos(theta), np.sin(theta)
        R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float32)
        
        centered = xyz[indices] - pivot
        xyz[indices] = (R @ centered.T).T + pivot
        
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
    
    return xyz, quats


def write_subsampled_ply(v, xyz, quats, idx, output_path):
    """Write subsampled PLY"""
    n = len(idx)
    
    dtype = [
        ('x', 'f4'), ('y', 'f4'), ('z', 'f4'),
        ('nx', 'f4'), ('ny', 'f4'), ('nz', 'f4'),
        ('f_dc_0', 'f4'), ('f_dc_1', 'f4'), ('f_dc_2', 'f4'),
        ('opacity', 'f4'),
        ('scale_0', 'f4'), ('scale_1', 'f4'), ('scale_2', 'f4'),
        ('rot_0', 'f4'), ('rot_1', 'f4'), ('rot_2', 'f4'), ('rot_3', 'f4'),
    ]
    
    vertices = np.zeros(n, dtype=dtype)
    vertices['x'] = xyz[idx, 0]
    vertices['y'] = xyz[idx, 1]
    vertices['z'] = xyz[idx, 2]
    vertices['nx'] = 0
    vertices['ny'] = 0
    vertices['nz'] = 0
    vertices['f_dc_0'] = v['f_dc_0'][idx]
    vertices['f_dc_1'] = v['f_dc_1'][idx]
    vertices['f_dc_2'] = v['f_dc_2'][idx]
    vertices['opacity'] = v['opacity'][idx]
    vertices['scale_0'] = v['scale_0'][idx]
    vertices['scale_1'] = v['scale_1'][idx]
    vertices['scale_2'] = v['scale_2'][idx]
    vertices['rot_0'] = quats[idx, 0]
    vertices['rot_1'] = quats[idx, 1]
    vertices['rot_2'] = quats[idx, 2]
    vertices['rot_3'] = quats[idx, 3]
    
    el = PlyElement.describe(vertices, 'vertex')
    PlyData([el]).write(output_path)
    
    size_mb = Path(output_path).stat().st_size / 1024 / 1024
    print(f"  Wrote {output_path} ({n:,} gaussians, {size_mb:.1f} MB)")


def main():
    ply_path = "/home/ubuntu/datasets/sage_batch_walking/0001_839920_seq_006_fruit/gs_output_15k/point_cloud/iteration_15000/point_cloud.ply"
    labels_path = "/home/ubuntu/datasets/scenes/0001_839920/labels.json"
    output_dir = Path("/home/ubuntu/.openclaw/workspace")
    
    print("Loading...")
    v, n = load_splat(ply_path)
    print(f"  {n:,} gaussians")
    
    xyz = np.stack([v['x'], v['y'], v['z']], axis=1).astype(np.float32)
    quats = np.stack([v['rot_0'], v['rot_1'], v['rot_2'], v['rot_3']], axis=1).astype(np.float32)
    
    # Segment
    objects = segment_objects(xyz, labels_path)
    
    target = None
    for label in ['chair', 'Multi person sofa', 'table']:
        candidates = [o for o in objects if o['label'] == label and len(o['indices']) > 5000]
        if candidates:
            target = max(candidates, key=lambda o: len(o['indices']))
            break
    
    if not target:
        target = max(objects, key=lambda o: len(o['indices']))
    
    print(f"Target: {target['label']} ({len(target['indices']):,} gaussians)")
    
    # Subsample - keep 100k points total, but keep all target object points
    subsample = 100000
    other_idx = np.setdiff1d(np.arange(n), target['indices'])
    n_other = min(subsample - len(target['indices']), len(other_idx))
    other_sample = np.random.choice(other_idx, n_other, replace=False)
    idx = np.sort(np.concatenate([other_sample, target['indices']]))
    
    print(f"Subsampled to {len(idx):,} gaussians")
    
    # BEFORE
    print("\nWriting BEFORE...")
    write_subsampled_ply(v, xyz, quats, idx, output_dir / "small_before.ply")
    
    # AFTER
    print("\nTransforming...")
    xyz_new, quats_new = transform_indices(v, target['indices'], 
                                           translation=[1.5, 1.0, 0.3],
                                           rotation_z_deg=45)
    
    print("\nWriting AFTER...")
    write_subsampled_ply(v, xyz_new, quats_new, idx, output_dir / "small_after.ply")
    
    print("\nDone!")


if __name__ == "__main__":
    main()
