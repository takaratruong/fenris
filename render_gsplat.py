#!/usr/bin/env python3
"""
Proper Gaussian Splatting Render using gsplat
Creates actual splat rendering (not point clouds)
"""

import torch
import numpy as np
from PIL import Image
from plyfile import PlyData
import json
from pathlib import Path

# gsplat imports
from gsplat import rasterization


def load_gaussian_splat(ply_path: str, device="cuda"):
    """Load gaussian splat PLY and return tensors for rendering"""
    ply = PlyData.read(ply_path)
    v = ply['vertex'].data
    n = len(v['x'])
    
    # Positions
    means = torch.tensor(
        np.stack([v['x'], v['y'], v['z']], axis=1),
        dtype=torch.float32, device=device
    )
    
    # Quaternions (wxyz in file)
    quats = torch.tensor(
        np.stack([v['rot_0'], v['rot_1'], v['rot_2'], v['rot_3']], axis=1),
        dtype=torch.float32, device=device
    )
    quats = quats / quats.norm(dim=1, keepdim=True)
    
    # Scales (log space in file)
    scales = torch.tensor(
        np.stack([v['scale_0'], v['scale_1'], v['scale_2']], axis=1),
        dtype=torch.float32, device=device
    )
    scales = torch.exp(scales)
    
    # Opacities (logit in file)
    opacities = torch.tensor(v['opacity'], dtype=torch.float32, device=device)
    opacities = torch.sigmoid(opacities)
    
    # Colors from SH (just DC term for now)
    SH_C0 = 0.28209479177387814
    sh_dc = np.stack([v['f_dc_0'], v['f_dc_1'], v['f_dc_2']], axis=1)
    colors = torch.tensor(
        np.clip(sh_dc * SH_C0 + 0.5, 0, 1),
        dtype=torch.float32, device=device
    )
    
    print(f"Loaded {n} gaussians")
    print(f"  Position range: {means.min(dim=0).values.cpu().numpy()} to {means.max(dim=0).values.cpu().numpy()}")
    
    return means, quats, scales, opacities, colors


def segment_by_bbox(means, labels_json, margin=0.05, device="cuda"):
    """Segment gaussians by bounding boxes"""
    means_np = means.cpu().numpy()
    
    with open(labels_json) as f:
        labels = json.load(f)
    
    objects = []
    for item in labels:
        if 'bounding_box' not in item:
            continue
        pts = np.array([[c['x'], c['y'], c['z']] for c in item['bounding_box']])
        bbox_min, bbox_max = pts.min(axis=0), pts.max(axis=0)
        
        mask = np.all((means_np >= bbox_min - margin) & (means_np <= bbox_max + margin), axis=1)
        indices = np.where(mask)[0]
        
        if len(indices) > 100:
            objects.append({
                'id': item['ins_id'],
                'label': item['label'],
                'indices': torch.tensor(indices, device=device, dtype=torch.long),
                'center': torch.tensor((bbox_min + bbox_max) / 2, dtype=torch.float32, device=device)
            })
    
    return objects


def make_camera(position, look_at, up=None, fov_deg=60, width=1280, height=720, device="cuda"):
    """Create camera matrices for gsplat"""
    if up is None:
        up = torch.tensor([0., 0., 1.], device=device)
    
    position = torch.as_tensor(position, dtype=torch.float32, device=device)
    look_at = torch.as_tensor(look_at, dtype=torch.float32, device=device)
    up = torch.as_tensor(up, dtype=torch.float32, device=device)
    
    # Camera basis
    forward = look_at - position
    forward = forward / forward.norm()
    right = torch.cross(forward, up)
    right = right / right.norm()
    true_up = torch.cross(right, forward)
    
    # View matrix (world to camera)
    R = torch.stack([right, true_up, -forward], dim=0)  # [3, 3]
    t = -R @ position  # [3]
    
    viewmat = torch.eye(4, device=device)
    viewmat[:3, :3] = R
    viewmat[:3, 3] = t
    
    # Intrinsics
    fov_rad = np.radians(fov_deg)
    fy = height / (2 * np.tan(fov_rad / 2))
    fx = fy  # square pixels
    cx, cy = width / 2, height / 2
    
    K = torch.tensor([
        [fx, 0, cx],
        [0, fy, cy],
        [0, 0, 1]
    ], dtype=torch.float32, device=device)
    
    return viewmat, K, width, height


def render_gsplat(means, quats, scales, opacities, colors, viewmat, K, width, height, 
                  bg_color=(1., 1., 1.), device="cuda"):
    """Render using gsplat rasterization"""
    
    # gsplat expects specific format
    N = means.shape[0]
    
    # Background
    backgrounds = torch.tensor([bg_color], dtype=torch.float32, device=device)
    
    # Rasterize
    render_colors, render_alphas, meta = rasterization(
        means=means,  # [N, 3]
        quats=quats,  # [N, 4]
        scales=scales,  # [N, 3]
        opacities=opacities,  # [N]
        colors=colors,  # [N, 3]
        viewmats=viewmat.unsqueeze(0),  # [1, 4, 4]
        Ks=K.unsqueeze(0),  # [1, 3, 3]
        width=width,
        height=height,
        packed=False,
        backgrounds=backgrounds,
    )
    
    # render_colors: [1, H, W, 3]
    img = render_colors[0].clamp(0, 1)
    return img


def transform_object(means, quats, indices, translation, rotation_z_deg=0):
    """Transform object: translate and rotate around Z axis"""
    pivot = means[indices].mean(dim=0)
    
    if rotation_z_deg != 0:
        theta = torch.tensor(np.radians(rotation_z_deg), device=means.device)
        c, s = torch.cos(theta), torch.sin(theta)
        R = torch.tensor([
            [c, -s, 0],
            [s, c, 0],
            [0, 0, 1]
        ], dtype=torch.float32, device=means.device)
        
        # Rotate positions around pivot
        centered = means[indices] - pivot
        means[indices] = (R @ centered.T).T + pivot
        
        # Rotate quaternions
        # Rotation around Z: quaternion (cos(θ/2), 0, 0, sin(θ/2)) in wxyz
        half = theta / 2
        rot_quat = torch.tensor([torch.cos(half), 0, 0, torch.sin(half)], device=means.device)
        
        # Quaternion multiplication for each gaussian
        q = quats[indices]  # [M, 4] wxyz
        w1, x1, y1, z1 = rot_quat
        w2, x2, y2, z2 = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
        
        quats[indices] = torch.stack([
            w1*w2 - x1*x2 - y1*y2 - z1*z2,
            w1*x2 + x1*w2 + y1*z2 - z1*y2,
            w1*y2 - x1*z2 + y1*w2 + z1*x2,
            w1*z2 + x1*y2 - y1*x2 + z1*w2
        ], dim=1)
        quats[indices] = quats[indices] / quats[indices].norm(dim=1, keepdim=True)
    
    # Translate
    translation = torch.as_tensor(translation, dtype=torch.float32, device=means.device)
    means[indices] += translation
    
    return means, quats


def main():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Using device: {device}")
    
    # Paths
    ply_path = "/home/ubuntu/datasets/sage_batch_walking/0001_839920_seq_006_fruit/gs_output_15k/point_cloud/iteration_15000/point_cloud.ply"
    labels_path = "/home/ubuntu/datasets/scenes/0001_839920/labels.json"
    output_dir = Path("/home/ubuntu/.openclaw/workspace")
    
    print("\n" + "=" * 60)
    print("Loading gaussian splat...")
    print("=" * 60)
    means, quats, scales, opacities, colors = load_gaussian_splat(ply_path, device)
    
    # Keep originals for comparison
    means_orig = means.clone()
    quats_orig = quats.clone()
    
    # Segment objects
    print("\nSegmenting objects...")
    objects = segment_by_bbox(means, labels_path, device=device)
    
    # Print what we found
    from collections import Counter
    label_counts = Counter(o['label'] for o in objects)
    print("Found objects:")
    for label, count in label_counts.most_common(10):
        total_pts = sum(len(o['indices']) for o in objects if o['label'] == label)
        print(f"  {label}: {count} ({total_pts:,} gaussians)")
    
    # Pick the biggest chair
    target = None
    for label in ['chair', 'Multi person sofa', 'table', 'wardrobe']:
        candidates = [o for o in objects if o['label'] == label and len(o['indices']) > 5000]
        if candidates:
            target = max(candidates, key=lambda o: len(o['indices']))
            break
    
    if not target:
        target = max(objects, key=lambda o: len(o['indices']))
    
    print(f"\nSelected: {target['label']} ({len(target['indices']):,} gaussians)")
    
    # Set up camera looking at the scene
    scene_center = means.mean(dim=0)
    obj_center = means[target['indices']].mean(dim=0)
    
    # Camera position - place it to see the object well
    cam_distance = 6.0
    cam_height = 3.0
    cam_pos = obj_center + torch.tensor([cam_distance, -cam_distance * 0.7, cam_height], device=device)
    
    print(f"\nCamera at: {cam_pos.cpu().numpy()}")
    print(f"Looking at: {obj_center.cpu().numpy()}")
    
    # Create camera
    viewmat, K, W, H = make_camera(cam_pos, obj_center, width=1280, height=720, fov_deg=55, device=device)
    
    # Render BEFORE
    print("\n" + "=" * 60)
    print("Rendering BEFORE...")
    print("=" * 60)
    
    img_before = render_gsplat(means_orig, quats_orig, scales, opacities, colors, viewmat, K, W, H, device=device)
    img_before_np = (img_before.cpu().numpy() * 255).astype(np.uint8)
    Image.fromarray(img_before_np).save(output_dir / "gsplat_before.png")
    print("Saved: gsplat_before.png")
    
    # Transform the object
    print("\n" + "=" * 60)
    print(f"Moving {target['label']}...")
    print("  Translation: [1.5, 1.0, 0.3]")
    print("  Rotation: 45° around Z")
    print("=" * 60)
    
    means, quats = transform_object(
        means, quats, target['indices'],
        translation=[1.5, 1.0, 0.3],
        rotation_z_deg=45
    )
    
    # Render AFTER
    print("\n" + "=" * 60)
    print("Rendering AFTER...")
    print("=" * 60)
    
    img_after = render_gsplat(means, quats, scales, opacities, colors, viewmat, K, W, H, device=device)
    img_after_np = (img_after.cpu().numpy() * 255).astype(np.uint8)
    Image.fromarray(img_after_np).save(output_dir / "gsplat_after.png")
    print("Saved: gsplat_after.png")
    
    # Side by side comparison
    comparison = np.concatenate([img_before_np, img_after_np], axis=1)
    Image.fromarray(comparison).save(output_dir / "gsplat_comparison.png")
    print("Saved: gsplat_comparison.png")
    
    # Also make a labeled version
    from PIL import ImageDraw, ImageFont
    comp_img = Image.fromarray(comparison)
    draw = ImageDraw.Draw(comp_img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
    except:
        font = ImageFont.load_default()
    
    draw.text((50, 20), "BEFORE", fill=(255, 50, 50), font=font)
    draw.text((W + 50, 20), "AFTER (moved + rotated)", fill=(50, 255, 50), font=font)
    comp_img.save(output_dir / "gsplat_comparison_labeled.png")
    print("Saved: gsplat_comparison_labeled.png")
    
    print("\n" + "=" * 60)
    print("DONE!")
    print("=" * 60)


if __name__ == "__main__":
    main()
