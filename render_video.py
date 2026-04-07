#!/usr/bin/env python3
"""
Render animated video of gaussian splat object manipulation
Shows the chair moving and rotating smoothly
"""

import numpy as np
from PIL import Image
from plyfile import PlyData
import json
from collections import Counter
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
import subprocess
from pathlib import Path


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
    
    print(f"Loaded {len(xyz)} gaussians")
    return xyz, colors, opacity


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
            })
    
    return objects


def render_frame(xyz, colors, opacity, obj_indices, elev, azim, subsample_idx, 
                 xlim, ylim, zlim, figsize=(12, 10)):
    """Render a single frame"""
    fig = plt.figure(figsize=figsize)
    ax = fig.add_subplot(111, projection='3d')
    
    # Use precomputed subsample
    idx = subsample_idx
    
    # Colors with opacity
    c = colors[idx].copy()
    alpha = opacity[idx].clip(0.1, 1.0)
    
    rgba = np.zeros((len(idx), 4))
    rgba[:, :3] = c
    rgba[:, 3] = alpha * 0.7
    
    # Highlight moved object
    highlight_mask = np.isin(idx, obj_indices)
    rgba[highlight_mask, :3] = [1, 0.2, 0.2]  # Red
    rgba[highlight_mask, 3] = 0.95
    
    ax.scatter(
        xyz[idx, 0],
        xyz[idx, 1],
        xyz[idx, 2],
        c=rgba,
        s=0.8,
        alpha=0.8
    )
    
    ax.set_xlim(xlim)
    ax.set_ylim(ylim)
    ax.set_zlim(zlim)
    ax.set_xlabel('X')
    ax.set_ylabel('Y')
    ax.set_zlabel('Z')
    ax.view_init(elev=elev, azim=azim)
    ax.set_title('Gaussian Splat Object Manipulation', fontsize=14, fontweight='bold')
    
    # Remove background for cleaner look
    ax.xaxis.pane.fill = False
    ax.yaxis.pane.fill = False
    ax.zaxis.pane.fill = False
    ax.xaxis.pane.set_edgecolor('lightgray')
    ax.yaxis.pane.set_edgecolor('lightgray')
    ax.zaxis.pane.set_edgecolor('lightgray')
    
    plt.tight_layout()
    return fig


def interpolate_transform(xyz_orig, indices, t, max_translation, max_rotation_deg):
    """Interpolate transformation at time t (0 to 1)"""
    xyz = xyz_orig.copy()
    
    # Current transform values
    translation = max_translation * t
    rotation_deg = max_rotation_deg * t
    
    # Apply transform
    pivot = xyz_orig[indices].mean(axis=0)
    
    if rotation_deg != 0:
        theta = np.radians(rotation_deg)
        c, s = np.cos(theta), np.sin(theta)
        R = np.array([
            [c, -s, 0],
            [s, c, 0],
            [0, 0, 1]
        ])
        
        centered = xyz[indices] - pivot
        xyz[indices] = (R @ centered.T).T + pivot
    
    xyz[indices] += translation
    return xyz


def main():
    # Paths
    ply_path = "/home/ubuntu/datasets/sage_batch_walking/0001_839920_seq_006_fruit/gs_output_15k/point_cloud/iteration_15000/point_cloud.ply"
    labels_path = "/home/ubuntu/datasets/scenes/0001_839920/labels.json"
    output_dir = Path("/home/ubuntu/.openclaw/workspace/video_frames")
    output_dir.mkdir(exist_ok=True)
    
    print("=" * 60)
    print("Loading Gaussian Splat")
    print("=" * 60)
    xyz_orig, colors, opacity = load_splat(ply_path)
    
    print("\nSegmenting objects...")
    objects = segment_objects(xyz_orig, labels_path)
    
    # Pick a chair
    target = None
    for label in ['chair', 'Multi person sofa', 'table']:
        candidates = [o for o in objects if o['label'] == label and len(o['indices']) > 3000]
        if candidates:
            target = max(candidates, key=lambda o: len(o['indices']))
            break
    
    if not target:
        target = max(objects, key=lambda o: len(o['indices']))
    
    print(f"\nSelected: {target['label']} ({len(target['indices'])} gaussians)")
    
    # Precompute subsample (keep all object points)
    n = len(xyz_orig)
    subsample_size = 60000
    other_idx = np.setdiff1d(np.arange(n), target['indices'])
    other_sample = np.random.choice(other_idx, min(subsample_size - len(target['indices']), len(other_idx)), replace=False)
    subsample_idx = np.concatenate([other_sample, target['indices']])
    
    # Compute fixed view bounds based on original + transformed positions
    max_translation = np.array([2.0, 1.5, 0.5])
    max_rotation_deg = 60
    
    # Get bounds
    xyz_end = interpolate_transform(xyz_orig, target['indices'], 1.0, max_translation, max_rotation_deg)
    all_xyz = np.vstack([xyz_orig, xyz_end])
    
    max_range = np.array([
        all_xyz[:, 0].max() - all_xyz[:, 0].min(),
        all_xyz[:, 1].max() - all_xyz[:, 1].min(),
        all_xyz[:, 2].max() - all_xyz[:, 2].min()
    ]).max() / 2.0 * 1.1
    
    mid_x = (all_xyz[:, 0].max() + all_xyz[:, 0].min()) / 2
    mid_y = (all_xyz[:, 1].max() + all_xyz[:, 1].min()) / 2
    mid_z = (all_xyz[:, 2].max() + all_xyz[:, 2].min()) / 2
    
    xlim = (mid_x - max_range, mid_x + max_range)
    ylim = (mid_y - max_range, mid_y + max_range)
    zlim = (mid_z - max_range, mid_z + max_range)
    
    # Animation parameters
    n_frames = 90  # 3 seconds at 30fps
    fps = 30
    
    # Three phases:
    # 1. Static view (15 frames) - show original
    # 2. Object moving (45 frames) - animate transform
    # 3. Camera rotate (30 frames) - orbit around
    
    print(f"\n{'=' * 60}")
    print(f"Rendering {n_frames} frames...")
    print("=" * 60)
    
    for i in range(n_frames):
        print(f"  Frame {i+1}/{n_frames}", end='\r')
        
        # Determine phase and parameters
        if i < 15:
            # Phase 1: Static original
            t = 0.0
            azim = 135
        elif i < 60:
            # Phase 2: Animate transform
            phase_progress = (i - 15) / 45
            t = phase_progress
            azim = 135
        else:
            # Phase 3: Orbit camera
            t = 1.0
            phase_progress = (i - 60) / 30
            azim = 135 + phase_progress * 90  # Rotate 90 degrees
        
        # Get transformed positions
        xyz = interpolate_transform(xyz_orig, target['indices'], t, max_translation, max_rotation_deg)
        
        # Render
        fig = render_frame(
            xyz, colors, opacity, target['indices'],
            elev=25, azim=azim, subsample_idx=subsample_idx,
            xlim=xlim, ylim=ylim, zlim=zlim
        )
        
        # Save frame
        fig.savefig(output_dir / f"frame_{i:04d}.png", dpi=100, facecolor='white')
        plt.close(fig)
    
    print(f"\n\nFrames saved to {output_dir}")
    
    # Combine frames into video with ffmpeg
    print("\nEncoding video with ffmpeg...")
    video_path = "/home/ubuntu/.openclaw/workspace/gaussian_splat_manipulation.mp4"
    
    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", str(output_dir / "frame_%04d.png"),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", "23",
        "-preset", "medium",
        video_path
    ]
    
    subprocess.run(cmd, check=True, capture_output=True)
    
    print(f"\n{'=' * 60}")
    print("DONE!")
    print(f"{'=' * 60}")
    print(f"\nVideo saved: {video_path}")
    
    # Get file size
    size_mb = Path(video_path).stat().st_size / (1024 * 1024)
    print(f"Size: {size_mb:.2f} MB")


if __name__ == "__main__":
    main()
