#!/usr/bin/env python3
"""
Gaussian Splat Rendering via PyTorch3D Point Cloud Renderer
Renders gaussian splats as oriented ellipsoids
"""

import torch
import numpy as np
from PIL import Image
from plyfile import PlyData
import json
from collections import Counter
from pytorch3d.structures import Pointclouds
from pytorch3d.renderer import (
    PerspectiveCameras,
    PointsRasterizationSettings,
    PointsRenderer,
    PointsRasterizer,
    AlphaCompositor,
    NormWeightedCompositor
)


def load_gaussian_splat(ply_path: str, device="cuda"):
    """Load gaussian splat and return render-ready tensors"""
    ply = PlyData.read(ply_path)
    v = ply['vertex'].data
    
    # Positions
    xyz = torch.tensor(np.stack([v['x'], v['y'], v['z']], axis=1), dtype=torch.float32, device=device)
    
    # Colors from SH DC
    SH_C0 = 0.28209479177387814
    sh_dc = np.stack([v['f_dc_0'], v['f_dc_1'], v['f_dc_2']], axis=1)
    colors = torch.tensor(np.clip(sh_dc * SH_C0 + 0.5, 0, 1), dtype=torch.float32, device=device)
    
    # Opacity
    opacity_logit = v['opacity']
    opacity = torch.tensor(1 / (1 + np.exp(-opacity_logit)), dtype=torch.float32, device=device)
    
    # Scale (for point size)
    scale_log = np.stack([v['scale_0'], v['scale_1'], v['scale_2']], axis=1)
    scales = np.exp(scale_log)
    avg_scale = torch.tensor(scales.mean(axis=1), dtype=torch.float32, device=device)
    
    print(f"Loaded {len(xyz)} gaussians")
    return xyz, colors, opacity, avg_scale


def segment_objects(xyz: torch.Tensor, labels_json: str, margin: float = 0.05):
    """Segment by bounding boxes"""
    xyz_np = xyz.cpu().numpy()
    
    with open(labels_json) as f:
        labels = json.load(f)
    
    objects = []
    for item in labels:
        if 'bounding_box' not in item:
            continue
        pts = np.array([[c['x'], c['y'], c['z']] for c in item['bounding_box']])
        bbox_min, bbox_max = pts.min(axis=0), pts.max(axis=0)
        
        mask = np.all((xyz_np >= bbox_min - margin) & (xyz_np <= bbox_max + margin), axis=1)
        indices = np.where(mask)[0]
        
        if len(indices) > 10:
            objects.append({
                'id': item['ins_id'],
                'label': item['label'],
                'indices': torch.tensor(indices, device=xyz.device),
                'center': torch.tensor((bbox_min + bbox_max) / 2, dtype=torch.float32, device=xyz.device)
            })
    
    return objects


def render_point_cloud(
    xyz: torch.Tensor,
    colors: torch.Tensor,
    camera_pos: torch.Tensor,
    look_at: torch.Tensor,
    image_size: int = 1024,
    point_radius: float = 0.01,
    device="cuda"
) -> np.ndarray:
    """Render point cloud from a viewpoint"""
    
    # Compute camera rotation
    up = torch.tensor([0.0, 0.0, 1.0], device=device)
    forward = look_at - camera_pos
    forward = forward / forward.norm()
    right = torch.cross(forward, up)
    right = right / right.norm()
    up = torch.cross(right, forward)
    
    # PyTorch3D uses row vectors, and camera looks down -Z
    R = torch.stack([right, up, -forward], dim=0).unsqueeze(0)  # [1, 3, 3]
    T = (-R.squeeze(0) @ camera_pos).unsqueeze(0)  # [1, 3]
    
    # Camera
    cameras = PerspectiveCameras(
        R=R,
        T=T,
        focal_length=1.5,
        principal_point=((0.0, 0.0),),
        image_size=((image_size, image_size),),
        device=device
    )
    
    # Rasterizer settings
    raster_settings = PointsRasterizationSettings(
        image_size=image_size,
        radius=point_radius,
        points_per_pixel=10
    )
    
    # Renderer
    rasterizer = PointsRasterizer(cameras=cameras, raster_settings=raster_settings)
    renderer = PointsRenderer(
        rasterizer=rasterizer,
        compositor=AlphaCompositor(background_color=(1, 1, 1))
    )
    
    # Create point cloud
    point_cloud = Pointclouds(
        points=[xyz],
        features=[colors]
    )
    
    # Render
    images = renderer(point_cloud)
    
    # Convert to numpy
    img = images[0, ..., :3].cpu().numpy()
    img = np.clip(img, 0, 1)
    return (img * 255).astype(np.uint8)


def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")
    
    # Paths
    ply_path = "/home/ubuntu/datasets/sage_batch_walking/0001_839920_seq_006_fruit/gs_output_15k/point_cloud/iteration_15000/point_cloud.ply"
    labels_path = "/home/ubuntu/datasets/scenes/0001_839920/labels.json"
    
    # Load
    print("\n" + "=" * 60)
    print("Loading gaussian splat...")
    print("=" * 60)
    xyz, colors, opacity, scales = load_gaussian_splat(ply_path, device)
    
    # Apply opacity to colors (pre-multiply)
    colors = colors * opacity.unsqueeze(-1)
    
    # Segment
    print("\nSegmenting objects...")
    objects = segment_objects(xyz, labels_path)
    
    label_counts = Counter(o['label'] for o in objects)
    print("\nFound objects:")
    for label, count in label_counts.most_common(10):
        print(f"  {label}: {count}")
    
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
    
    # Camera setup
    obj_center = xyz[target['indices']].mean(dim=0)
    cam_offset = torch.tensor([5.0, -5.0, 4.0], device=device)
    camera_pos = obj_center + cam_offset
    
    print(f"\nCamera: {camera_pos.cpu().numpy()}")
    print(f"Looking at: {obj_center.cpu().numpy()}")
    
    # Render BEFORE
    print("\n" + "=" * 60)
    print("Rendering BEFORE...")
    print("=" * 60)
    
    img_before = render_point_cloud(
        xyz, colors, camera_pos, obj_center,
        image_size=1024, point_radius=0.008, device=device
    )
    Image.fromarray(img_before).save("/home/ubuntu/.openclaw/workspace/pt3d_before.png")
    print("Saved: pt3d_before.png")
    
    # Transform object
    print(f"\n{'=' * 60}")
    print(f"Moving {target['label']}...")
    print("  Translation: [2.0, 1.0, 0.5]")
    print("  Rotation: 45°")
    print("=" * 60)
    
    # Get indices
    idx = target['indices']
    pivot = xyz[idx].mean(dim=0)
    
    # Rotate around Z
    theta = torch.tensor(np.radians(45), device=device)
    c, s = torch.cos(theta), torch.sin(theta)
    R = torch.tensor([
        [c, -s, 0],
        [s, c, 0],
        [0, 0, 1]
    ], dtype=torch.float32, device=device)
    
    # Apply rotation
    centered = xyz[idx] - pivot
    xyz[idx] = (R @ centered.T).T + pivot
    
    # Apply translation
    translation = torch.tensor([2.0, 1.0, 0.5], device=device)
    xyz[idx] += translation
    
    print(f"  New center: {xyz[idx].mean(dim=0).cpu().numpy()}")
    
    # Render AFTER
    print("\n" + "=" * 60)
    print("Rendering AFTER...")
    print("=" * 60)
    
    img_after = render_point_cloud(
        xyz, colors, camera_pos, obj_center,  # Same camera, object has moved
        image_size=1024, point_radius=0.008, device=device
    )
    Image.fromarray(img_after).save("/home/ubuntu/.openclaw/workspace/pt3d_after.png")
    print("Saved: pt3d_after.png")
    
    # Side by side
    comparison = np.concatenate([img_before, img_after], axis=1)
    Image.fromarray(comparison).save("/home/ubuntu/.openclaw/workspace/pt3d_comparison.png")
    print("Saved: pt3d_comparison.png")
    
    print("\n" + "=" * 60)
    print("DONE!")
    print("=" * 60)


if __name__ == "__main__":
    main()
