#!/usr/bin/env python3
"""
Simple Gaussian Splat Visualization with Matplotlib
Shows before/after object manipulation clearly
"""

import numpy as np
from PIL import Image
from plyfile import PlyData
import json
from collections import Counter
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D


def load_splat(ply_path: str):
    """Load gaussian splat"""
    ply = PlyData.read(ply_path)
    v = ply['vertex'].data
    
    xyz = np.stack([v['x'], v['y'], v['z']], axis=1).astype(np.float32)
    
    # Colors from SH DC
    SH_C0 = 0.28209479177387814
    sh_dc = np.stack([v['f_dc_0'], v['f_dc_1'], v['f_dc_2']], axis=1)
    colors = np.clip(sh_dc * SH_C0 + 0.5, 0, 1)
    
    # Opacity
    opacity = 1 / (1 + np.exp(-v['opacity']))
    
    # Rotation (for later)
    quat = np.stack([v['rot_0'], v['rot_1'], v['rot_2'], v['rot_3']], axis=1)
    quat = quat / np.linalg.norm(quat, axis=1, keepdims=True)
    
    print(f"Loaded {len(xyz)} gaussians")
    print(f"  Range: {xyz.min(axis=0)} to {xyz.max(axis=0)}")
    
    return xyz, colors, opacity, quat


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
        
        if len(indices) > 10:
            objects.append({
                'id': item['ins_id'],
                'label': item['label'],
                'indices': indices,
                'center': (bbox_min + bbox_max) / 2
            })
    
    return objects


def quat_multiply(q1, q2):
    """Quaternion multiply (wxyz format)"""
    w1, x1, y1, z1 = q1
    w2, x2, y2, z2 = q2
    return np.array([
        w1*w2 - x1*x2 - y1*y2 - z1*z2,
        w1*x2 + x1*w2 + y1*z2 - z1*y2,
        w1*y2 - x1*z2 + y1*w2 + z1*x2,
        w1*z2 + x1*y2 - y1*x2 + z1*w2
    ])


def transform_object(xyz, quat, indices, translation, rotation_z_deg=0):
    """Transform object positions and quaternions"""
    pivot = xyz[indices].mean(axis=0)
    
    if rotation_z_deg != 0:
        theta = np.radians(rotation_z_deg)
        c, s = np.cos(theta), np.sin(theta)
        R = np.array([
            [c, -s, 0],
            [s, c, 0],
            [0, 0, 1]
        ])
        
        # Rotate positions
        centered = xyz[indices] - pivot
        xyz[indices] = (R @ centered.T).T + pivot
        
        # Rotate quaternions
        R_quat = np.array([np.cos(theta/2), 0, 0, np.sin(theta/2)])  # wxyz
        for i in indices:
            quat[i] = quat_multiply(R_quat, quat[i])
            quat[i] /= np.linalg.norm(quat[i])
    
    xyz[indices] += translation
    return xyz, quat


def render_3d_view(xyz, colors, opacity, elev=25, azim=45, title="", 
                   highlight_indices=None, highlight_color='red',
                   subsample=50000, figsize=(10, 10)):
    """Render 3D view with matplotlib"""
    
    # Subsample for speed
    n = len(xyz)
    if n > subsample:
        idx = np.random.choice(n, subsample, replace=False)
        # Keep all highlighted points
        if highlight_indices is not None:
            idx = np.unique(np.concatenate([idx, highlight_indices]))
    else:
        idx = np.arange(n)
    
    fig = plt.figure(figsize=figsize)
    ax = fig.add_subplot(111, projection='3d')
    
    # Determine colors with opacity
    c = colors[idx].copy()
    alpha = opacity[idx].clip(0.1, 1.0)
    
    # Create RGBA
    rgba = np.zeros((len(idx), 4))
    rgba[:, :3] = c
    rgba[:, 3] = alpha * 0.8
    
    # Highlight object
    if highlight_indices is not None:
        highlight_mask = np.isin(idx, highlight_indices)
        rgba[highlight_mask, :3] = [1, 0.2, 0.2]  # Red
        rgba[highlight_mask, 3] = 0.9
    
    ax.scatter(
        xyz[idx, 0],
        xyz[idx, 1],
        xyz[idx, 2],
        c=rgba,
        s=1,
        alpha=0.8
    )
    
    ax.set_xlabel('X')
    ax.set_ylabel('Y')
    ax.set_zlabel('Z')
    ax.set_title(title, fontsize=14)
    ax.view_init(elev=elev, azim=azim)
    
    # Equal aspect ratio
    max_range = np.array([
        xyz[:, 0].max() - xyz[:, 0].min(),
        xyz[:, 1].max() - xyz[:, 1].min(),
        xyz[:, 2].max() - xyz[:, 2].min()
    ]).max() / 2.0
    
    mid_x = (xyz[:, 0].max() + xyz[:, 0].min()) / 2
    mid_y = (xyz[:, 1].max() + xyz[:, 1].min()) / 2
    mid_z = (xyz[:, 2].max() + xyz[:, 2].min()) / 2
    
    ax.set_xlim(mid_x - max_range, mid_x + max_range)
    ax.set_ylim(mid_y - max_range, mid_y + max_range)
    ax.set_zlim(mid_z - max_range, mid_z + max_range)
    
    return fig


def main():
    # Paths
    ply_path = "/home/ubuntu/datasets/sage_batch_walking/0001_839920_seq_006_fruit/gs_output_15k/point_cloud/iteration_15000/point_cloud.ply"
    labels_path = "/home/ubuntu/datasets/scenes/0001_839920/labels.json"
    output_dir = "/home/ubuntu/.openclaw/workspace"
    
    print("=" * 60)
    print("Loading Gaussian Splat")
    print("=" * 60)
    xyz, colors, opacity, quat = load_splat(ply_path)
    
    print("\nSegmenting objects...")
    objects = segment_objects(xyz, labels_path)
    
    label_counts = Counter(o['label'] for o in objects)
    print("\nFound objects:")
    for label, count in label_counts.most_common(10):
        n_pts = sum(len(o['indices']) for o in objects if o['label'] == label)
        print(f"  {label}: {count} instances ({n_pts} gaussians)")
    
    # Pick a good object
    target = None
    for label in ['chair', 'Multi person sofa', 'table']:
        candidates = [o for o in objects if o['label'] == label and len(o['indices']) > 3000]
        if candidates:
            target = max(candidates, key=lambda o: len(o['indices']))
            break
    
    if not target:
        target = max(objects, key=lambda o: len(o['indices']))
    
    print(f"\n{'=' * 60}")
    print(f"Selected: {target['label']} ({len(target['indices'])} gaussians)")
    print(f"{'=' * 60}")
    
    # Render BEFORE
    print("\nRendering BEFORE...")
    fig = render_3d_view(
        xyz, colors, opacity, 
        elev=30, azim=135, 
        title=f"BEFORE: Original Scene (highlighted: {target['label']})",
        highlight_indices=target['indices'],
        subsample=80000
    )
    fig.savefig(f"{output_dir}/simple_before.png", dpi=150, bbox_inches='tight')
    plt.close(fig)
    print("  Saved: simple_before.png")
    
    # Transform the object
    print(f"\nTransforming {target['label']}...")
    print("  Translation: [2.0, 1.5, 0.5]")
    print("  Rotation: 60° around Z")
    
    original_center = xyz[target['indices']].mean(axis=0).copy()
    
    xyz, quat = transform_object(
        xyz, quat, target['indices'],
        translation=np.array([2.0, 1.5, 0.5]),
        rotation_z_deg=60
    )
    
    new_center = xyz[target['indices']].mean(axis=0)
    print(f"  Original center: {original_center}")
    print(f"  New center: {new_center}")
    
    # Render AFTER
    print("\nRendering AFTER...")
    fig = render_3d_view(
        xyz, colors, opacity,
        elev=30, azim=135,
        title=f"AFTER: {target['label']} Moved + Rotated 60°",
        highlight_indices=target['indices'],
        subsample=80000
    )
    fig.savefig(f"{output_dir}/simple_after.png", dpi=150, bbox_inches='tight')
    plt.close(fig)
    print("  Saved: simple_after.png")
    
    # Create side-by-side comparison
    print("\nCreating comparison image...")
    img1 = Image.open(f"{output_dir}/simple_before.png")
    img2 = Image.open(f"{output_dir}/simple_after.png")
    
    # Make same size
    w = max(img1.width, img2.width)
    h = max(img1.height, img2.height)
    
    comparison = Image.new('RGB', (w * 2, h), 'white')
    comparison.paste(img1, (0, 0))
    comparison.paste(img2, (w, 0))
    comparison.save(f"{output_dir}/simple_comparison.png")
    print("  Saved: simple_comparison.png")
    
    print(f"\n{'=' * 60}")
    print("DONE!")
    print(f"{'=' * 60}")
    print(f"\nOutput files in {output_dir}:")
    print("  - simple_before.png")
    print("  - simple_after.png") 
    print("  - simple_comparison.png")


if __name__ == "__main__":
    main()
