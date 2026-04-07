#!/usr/bin/env python3
"""
Interactive Gaussian Splat Viewer using Viser
Lets you view before/after transformations in a web browser
"""

import numpy as np
from plyfile import PlyData
import json
import viser
import viser.transforms as tf
from pathlib import Path
import time


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
    opacity = (1 / (1 + np.exp(-v['opacity']))).astype(np.float32)
    
    print(f"  Loaded {len(xyz):,} gaussians")
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
        
        if len(indices) > 100:
            objects.append({'label': item['label'], 'indices': indices})
    
    return objects


def main():
    ply_path = "/home/ubuntu/datasets/sage_batch_walking/0001_839920_seq_006_fruit/gs_output_15k/point_cloud/iteration_15000/point_cloud.ply"
    labels_path = "/home/ubuntu/datasets/scenes/0001_839920/labels.json"
    
    print("=" * 60)
    print("Viser Gaussian Splat Viewer")
    print("=" * 60)
    
    # Load
    xyz, colors, opacity = load_splat(ply_path)
    
    # Segment
    print("\nSegmenting objects...")
    objects = segment_objects(xyz, labels_path)
    
    # Pick target
    from collections import Counter
    label_counts = Counter(o['label'] for o in objects)
    print("Found:", dict(label_counts.most_common(5)))
    
    target = None
    for label in ['chair', 'Multi person sofa', 'table']:
        candidates = [o for o in objects if o['label'] == label and len(o['indices']) > 5000]
        if candidates:
            target = max(candidates, key=lambda o: len(o['indices']))
            break
    
    if not target:
        target = max(objects, key=lambda o: len(o['indices']))
    
    print(f"\nSelected: {target['label']} ({len(target['indices']):,} gaussians)")
    
    # Create modified version
    xyz_after = xyz.copy()
    colors_after = colors.copy()
    
    # Transform
    translation = np.array([1.5, 1.0, 0.3])
    rotation_deg = 45
    
    pivot = xyz[target['indices']].mean(axis=0)
    theta = np.radians(rotation_deg)
    c, s = np.cos(theta), np.sin(theta)
    R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float32)
    
    centered = xyz_after[target['indices']] - pivot
    xyz_after[target['indices']] = (R @ centered.T).T + pivot + translation
    
    # Color the moved object red in after view
    colors_after[target['indices']] = [1.0, 0.3, 0.3]
    
    # Start viser server
    server = viser.ViserServer(host="0.0.0.0", port=8080)
    print("\n" + "=" * 60)
    print("Server running at http://localhost:8080")
    print("=" * 60)
    
    # Subsample for performance
    n = len(xyz)
    subsample = 100000
    if n > subsample:
        idx = np.random.choice(n, subsample, replace=False)
        # Keep all target object points
        idx = np.unique(np.concatenate([idx, target['indices']]))
    else:
        idx = np.arange(n)
    
    print(f"\nVisualing {len(idx):,} points")
    
    # Point cloud for BEFORE (blue tint for visibility)
    before_colors = colors[idx].copy()
    before_colors[np.isin(idx, target['indices'])] = [0.3, 0.3, 1.0]  # Blue for target
    
    pc_before = server.scene.add_point_cloud(
        "/before",
        points=xyz[idx],
        colors=before_colors,
        point_size=0.01,
        visible=True
    )
    
    # Point cloud for AFTER
    pc_after = server.scene.add_point_cloud(
        "/after",
        points=xyz_after[idx],
        colors=colors_after[idx],
        point_size=0.01,
        visible=False
    )
    
    # GUI controls
    with server.gui.add_folder("View"):
        show_before = server.gui.add_checkbox("Show BEFORE", initial_value=True)
        show_after = server.gui.add_checkbox("Show AFTER", initial_value=False)
    
    @show_before.on_update
    def _(_):
        pc_before.visible = show_before.value
    
    @show_after.on_update
    def _(_):
        pc_after.visible = show_after.value
    
    with server.gui.add_folder("Info"):
        server.gui.add_markdown(f"""
        **Scene:** SAGE-3D Interior
        **Total Gaussians:** {n:,}
        **Displayed:** {len(idx):,}
        
        **Selected Object:** {target['label']}
        **Object Gaussians:** {len(target['indices']):,}
        
        **Transform:**
        - Translation: [1.5, 1.0, 0.3]
        - Rotation: 45°
        
        BEFORE: Object shown in **blue**
        AFTER: Object shown in **red**
        """)
    
    print("\nServer ready! Toggle BEFORE/AFTER in the GUI.")
    print("Press Ctrl+C to stop.")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")


if __name__ == "__main__":
    main()
