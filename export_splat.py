#!/usr/bin/env python3
"""
Export gaussian splat to .splat format for web viewers
Creates before/after splat files viewable in antimatter15/splat or Luma
"""

import numpy as np
from plyfile import PlyData
import json
import struct
from pathlib import Path


def load_splat(ply_path: str):
    """Load gaussian splat PLY"""
    print(f"Loading {ply_path}...")
    ply = PlyData.read(ply_path)
    v = ply['vertex'].data
    
    xyz = np.stack([v['x'], v['y'], v['z']], axis=1).astype(np.float32)
    
    # Colors from SH DC  
    SH_C0 = 0.28209479177387814
    sh_dc = np.stack([v['f_dc_0'], v['f_dc_1'], v['f_dc_2']], axis=1)
    colors = np.clip(sh_dc * SH_C0 + 0.5, 0, 1).astype(np.float32)
    
    # Opacity
    opacity_logit = v['opacity'].astype(np.float32)
    opacity = 1 / (1 + np.exp(-opacity_logit))
    
    # Scale (exp of stored values)
    scales_log = np.stack([v['scale_0'], v['scale_1'], v['scale_2']], axis=1).astype(np.float32)
    scales = np.exp(scales_log)
    
    # Quaternions (wxyz in file)
    quats = np.stack([v['rot_0'], v['rot_1'], v['rot_2'], v['rot_3']], axis=1).astype(np.float32)
    quats = quats / np.linalg.norm(quats, axis=1, keepdims=True)
    
    print(f"  Loaded {len(xyz):,} gaussians")
    return xyz, colors, opacity, scales, quats, scales_log, opacity_logit


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


def transform_object(xyz, quats, indices, translation, rotation_z_deg=0):
    """Transform object positions and quaternions"""
    xyz = xyz.copy()
    quats = quats.copy()
    
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
    return xyz, quats


def write_ply(path, xyz, colors, opacity_logit, scales_log, quats):
    """Write standard 3DGS PLY file"""
    from plyfile import PlyData, PlyElement
    
    n = len(xyz)
    
    # Create structured array
    dtype = [
        ('x', 'f4'), ('y', 'f4'), ('z', 'f4'),
        ('nx', 'f4'), ('ny', 'f4'), ('nz', 'f4'),
        ('f_dc_0', 'f4'), ('f_dc_1', 'f4'), ('f_dc_2', 'f4'),
        ('opacity', 'f4'),
        ('scale_0', 'f4'), ('scale_1', 'f4'), ('scale_2', 'f4'),
        ('rot_0', 'f4'), ('rot_1', 'f4'), ('rot_2', 'f4'), ('rot_3', 'f4'),
    ]
    
    vertices = np.zeros(n, dtype=dtype)
    vertices['x'] = xyz[:, 0]
    vertices['y'] = xyz[:, 1]
    vertices['z'] = xyz[:, 2]
    vertices['nx'] = 0
    vertices['ny'] = 0
    vertices['nz'] = 0
    
    # Convert colors back to SH
    SH_C0 = 0.28209479177387814
    sh_dc = (colors - 0.5) / SH_C0
    vertices['f_dc_0'] = sh_dc[:, 0]
    vertices['f_dc_1'] = sh_dc[:, 1]
    vertices['f_dc_2'] = sh_dc[:, 2]
    
    vertices['opacity'] = opacity_logit
    vertices['scale_0'] = scales_log[:, 0]
    vertices['scale_1'] = scales_log[:, 1]
    vertices['scale_2'] = scales_log[:, 2]
    vertices['rot_0'] = quats[:, 0]
    vertices['rot_1'] = quats[:, 1]
    vertices['rot_2'] = quats[:, 2]
    vertices['rot_3'] = quats[:, 3]
    
    el = PlyElement.describe(vertices, 'vertex')
    PlyData([el]).write(path)
    print(f"  Wrote {path} ({n:,} gaussians)")


def write_splat_binary(path, xyz, colors, opacity, scales, quats):
    """
    Write .splat format (antimatter15/splat viewer format)
    Format: position (3xf32), scales (3xf32), color (4xu8), rotation (4xu8)
    """
    n = len(xyz)
    
    # Sort by opacity for better streaming
    order = np.argsort(-opacity)
    xyz = xyz[order]
    colors = colors[order]
    opacity = opacity[order]
    scales = scales[order]
    quats = quats[order]
    
    with open(path, 'wb') as f:
        for i in range(n):
            # Position (3 x float32)
            f.write(struct.pack('fff', *xyz[i]))
            
            # Scale (3 x float32) - log space
            f.write(struct.pack('fff', *np.log(scales[i])))
            
            # Color (4 x uint8) - RGBA
            r, g, b = (colors[i] * 255).clip(0, 255).astype(np.uint8)
            a = int(opacity[i] * 255)
            f.write(struct.pack('BBBB', r, g, b, a))
            
            # Rotation (4 x uint8) - normalized quaternion mapped to 0-255
            # Map from [-1, 1] to [0, 255]
            q = ((quats[i] + 1) / 2 * 255).clip(0, 255).astype(np.uint8)
            f.write(struct.pack('BBBB', *q))
    
    print(f"  Wrote {path} ({n:,} splats, {Path(path).stat().st_size / 1024 / 1024:.1f} MB)")


def main():
    output_dir = Path("/home/ubuntu/.openclaw/workspace")
    ply_path = "/home/ubuntu/datasets/sage_batch_walking/0001_839920_seq_006_fruit/gs_output_15k/point_cloud/iteration_15000/point_cloud.ply"
    labels_path = "/home/ubuntu/datasets/scenes/0001_839920/labels.json"
    
    print("=" * 60)
    print("Gaussian Splat Export")
    print("=" * 60)
    
    # Load
    xyz, colors, opacity, scales, quats, scales_log, opacity_logit = load_splat(ply_path)
    
    # Segment
    print("\nSegmenting objects...")
    objects = segment_objects(xyz, labels_path)
    
    from collections import Counter
    label_counts = Counter(o['label'] for o in objects)
    print("Found:")
    for label, count in label_counts.most_common(5):
        total = sum(len(o['indices']) for o in objects if o['label'] == label)
        print(f"  {label}: {count} ({total:,} gaussians)")
    
    # Pick target
    target = None
    for label in ['chair', 'Multi person sofa', 'table']:
        candidates = [o for o in objects if o['label'] == label and len(o['indices']) > 5000]
        if candidates:
            target = max(candidates, key=lambda o: len(o['indices']))
            break
    
    if not target:
        target = max(objects, key=lambda o: len(o['indices']))
    
    print(f"\nSelected: {target['label']} ({len(target['indices']):,} gaussians)")
    
    # Export BEFORE
    print("\n" + "=" * 60)
    print("Exporting BEFORE...")
    print("=" * 60)
    write_ply(output_dir / "scene_before.ply", xyz, colors, opacity_logit, scales_log, quats)
    write_splat_binary(output_dir / "scene_before.splat", xyz, colors, opacity, scales, quats)
    
    # Transform
    print("\n" + "=" * 60)
    print(f"Transforming {target['label']}...")
    print("  Translation: [1.5, 1.0, 0.3]")
    print("  Rotation: 45°")
    print("=" * 60)
    
    xyz_new, quats_new = transform_object(
        xyz, quats, target['indices'],
        translation=[1.5, 1.0, 0.3],
        rotation_z_deg=45
    )
    
    # Export AFTER
    print("\n" + "=" * 60)
    print("Exporting AFTER...")
    print("=" * 60)
    
    # Recompute scales_log for new quats (quats changed, scales unchanged)
    write_ply(output_dir / "scene_after.ply", xyz_new, colors, opacity_logit, scales_log, quats_new)
    write_splat_binary(output_dir / "scene_after.splat", xyz_new, colors, opacity, scales, quats_new)
    
    print("\n" + "=" * 60)
    print("DONE!")
    print("=" * 60)
    print(f"\nExported files:")
    print(f"  {output_dir / 'scene_before.ply'}")
    print(f"  {output_dir / 'scene_before.splat'}")
    print(f"  {output_dir / 'scene_after.ply'}")
    print(f"  {output_dir / 'scene_after.splat'}")
    print(f"\nView .splat files at: https://antimatter15.com/splat/")
    print(f"View .ply files in SuperSplat: https://playcanvas.com/supersplat/editor")


if __name__ == "__main__":
    main()
