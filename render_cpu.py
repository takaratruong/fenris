#!/usr/bin/env python3
"""
CPU-based Gaussian Splatting Renderer
Renders proper splats as 2D gaussians projected to screen
"""

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from plyfile import PlyData
import json
from pathlib import Path
from collections import Counter
from numba import jit, prange
import warnings
warnings.filterwarnings('ignore')


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
    
    # Scale (exp of stored values)
    scales = np.exp(np.stack([v['scale_0'], v['scale_1'], v['scale_2']], axis=1)).astype(np.float32)
    
    # Quaternions (wxyz)
    quats = np.stack([v['rot_0'], v['rot_1'], v['rot_2'], v['rot_3']], axis=1).astype(np.float32)
    quats = quats / np.linalg.norm(quats, axis=1, keepdims=True)
    
    print(f"  Loaded {len(xyz):,} gaussians")
    return xyz, colors, opacity, scales, quats


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
            objects.append({
                'label': item['label'],
                'indices': indices,
            })
    
    return objects


def quat_to_rotmat(q):
    """Convert wxyz quaternion to rotation matrix"""
    w, x, y, z = q
    return np.array([
        [1 - 2*(y*y + z*z), 2*(x*y - w*z), 2*(x*z + w*y)],
        [2*(x*y + w*z), 1 - 2*(x*x + z*z), 2*(y*z - w*x)],
        [2*(x*z - w*y), 2*(y*z + w*x), 1 - 2*(x*x + y*y)]
    ])


def transform_object(xyz, quats, indices, translation, rotation_z_deg=0):
    """Transform object positions and quaternions"""
    xyz = xyz.copy()
    quats = quats.copy()
    
    pivot = xyz[indices].mean(axis=0)
    
    if rotation_z_deg != 0:
        theta = np.radians(rotation_z_deg)
        c, s = np.cos(theta), np.sin(theta)
        R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float32)
        
        # Rotate positions
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


class Camera:
    """Simple pinhole camera"""
    def __init__(self, pos, look_at, up=None, fov=60, width=1280, height=720):
        self.pos = np.array(pos, dtype=np.float32)
        self.look_at = np.array(look_at, dtype=np.float32)
        self.up = np.array(up if up else [0, 0, 1], dtype=np.float32)
        self.width, self.height = width, height
        
        # Camera basis
        self.forward = self.look_at - self.pos
        self.forward /= np.linalg.norm(self.forward)
        self.right = np.cross(self.forward, self.up)
        self.right /= np.linalg.norm(self.right)
        self.true_up = np.cross(self.right, self.forward)
        
        # Intrinsics
        fov_rad = np.radians(fov)
        self.fy = height / (2 * np.tan(fov_rad / 2))
        self.fx = self.fy
        self.cx, self.cy = width / 2, height / 2
    
    def project(self, points):
        """Project 3D points to 2D"""
        # To camera space
        p = points - self.pos
        cam_coords = np.stack([
            np.dot(p, self.right),
            np.dot(p, -self.true_up),  # Y down
            np.dot(p, self.forward)
        ], axis=-1)
        
        # Project
        z = cam_coords[:, 2]
        valid = z > 0.1
        
        u = np.where(valid, self.fx * cam_coords[:, 0] / z + self.cx, -1000)
        v = np.where(valid, self.fy * cam_coords[:, 1] / z + self.cy, -1000)
        
        return np.stack([u, v], axis=-1), z, valid, cam_coords


@jit(nopython=True, parallel=True)
def render_splats_fast(
    uv, depth, valid, colors, opacity, scales_2d, 
    width, height, n_gaussians
):
    """Fast parallel splat rendering with numba"""
    # Output buffers
    img = np.ones((height, width, 3), dtype=np.float32)
    alpha_acc = np.zeros((height, width), dtype=np.float32)
    
    # Sort indices by depth (back to front)
    valid_idx = np.where(valid)[0]
    depths = depth[valid_idx]
    order = np.argsort(-depths)  # Back to front
    sorted_idx = valid_idx[order]
    
    # Render each splat
    for ii in range(len(sorted_idx)):
        i = sorted_idx[ii]
        
        u, v = uv[i, 0], uv[i, 1]
        if u < -50 or u > width + 50 or v < -50 or v > height + 50:
            continue
        
        r = scales_2d[i]
        if r < 0.5:
            r = 0.5
        if r > 100:
            r = 100
        
        alpha = opacity[i]
        if alpha < 0.01:
            continue
        
        color = colors[i]
        
        # Render gaussian as circle
        x_min = max(0, int(u - r * 3))
        x_max = min(width, int(u + r * 3) + 1)
        y_min = max(0, int(v - r * 3))
        y_max = min(height, int(v + r * 3) + 1)
        
        for py in range(y_min, y_max):
            for px in range(x_min, x_max):
                dx = px - u
                dy = py - v
                dist_sq = dx*dx + dy*dy
                
                # Gaussian falloff
                gauss = np.exp(-0.5 * dist_sq / (r * r))
                
                if gauss < 0.01:
                    continue
                
                a = gauss * alpha * (1 - alpha_acc[py, px])
                
                if a > 0.001:
                    img[py, px, 0] += a * (color[0] - img[py, px, 0])
                    img[py, px, 1] += a * (color[1] - img[py, px, 1])
                    img[py, px, 2] += a * (color[2] - img[py, px, 2])
                    alpha_acc[py, px] += a
    
    return np.clip(img, 0, 1)


def render_scene(xyz, colors, opacity, scales, quats, camera, subsample=200000):
    """Render gaussian splat scene"""
    n = len(xyz)
    
    # Subsample if too many
    if n > subsample:
        idx = np.random.choice(n, subsample, replace=False)
        xyz = xyz[idx]
        colors = colors[idx]
        opacity = opacity[idx]
        scales = scales[idx]
        quats = quats[idx]
    
    # Project to screen
    uv, depth, valid, cam_coords = camera.project(xyz)
    
    # Compute 2D scale (simplified - use max scale projected)
    avg_scale = scales.max(axis=1)  # Use max for more visible splats
    scales_2d = camera.fx * avg_scale * 3.0 / np.maximum(depth, 0.1)  # Scale up 3x
    scales_2d = np.clip(scales_2d, 1.0, 150)
    
    # Filter
    in_view = valid & (uv[:, 0] > -100) & (uv[:, 0] < camera.width + 100)
    in_view &= (uv[:, 1] > -100) & (uv[:, 1] < camera.height + 100)
    in_view &= (depth < 50) & (depth > 0.1)
    
    print(f"  {in_view.sum():,} gaussians in view")
    
    # Render
    img = render_splats_fast(
        uv.astype(np.float32),
        depth.astype(np.float32),
        in_view,
        colors.astype(np.float32),
        opacity.astype(np.float32),
        scales_2d.astype(np.float32),
        camera.width, camera.height,
        len(xyz)
    )
    
    return (img * 255).astype(np.uint8)


def main():
    output_dir = Path("/home/ubuntu/.openclaw/workspace")
    ply_path = "/home/ubuntu/datasets/sage_batch_walking/0001_839920_seq_006_fruit/gs_output_15k/point_cloud/iteration_15000/point_cloud.ply"
    labels_path = "/home/ubuntu/datasets/scenes/0001_839920/labels.json"
    
    print("=" * 60)
    print("CPU Gaussian Splat Renderer")
    print("=" * 60)
    
    # Load
    xyz, colors, opacity, scales, quats = load_splat(ply_path)
    
    # Segment objects
    print("\nSegmenting objects...")
    objects = segment_objects(xyz, labels_path)
    
    label_counts = Counter(o['label'] for o in objects)
    print("Found:")
    for label, count in label_counts.most_common(10):
        total = sum(len(o['indices']) for o in objects if o['label'] == label)
        print(f"  {label}: {count} ({total:,} gaussians)")
    
    # Pick target
    target = None
    for label in ['chair', 'Multi person sofa', 'table', 'wardrobe']:
        candidates = [o for o in objects if o['label'] == label and len(o['indices']) > 5000]
        if candidates:
            target = max(candidates, key=lambda o: len(o['indices']))
            break
    
    if not target:
        target = max(objects, key=lambda o: len(o['indices']))
    
    print(f"\nSelected: {target['label']} ({len(target['indices']):,} gaussians)")
    
    # Camera setup - closer to scene for better view
    scene_center = xyz.mean(axis=0)
    obj_center = xyz[target['indices']].mean(axis=0)
    cam_pos = scene_center + np.array([8, -8, 5])  # Further back, higher angle
    
    camera = Camera(cam_pos, scene_center, width=1280, height=720, fov=60)
    print(f"\nCamera: {cam_pos}")
    print(f"Look at: {obj_center}")
    
    # Render BEFORE
    print("\n" + "=" * 60)
    print("Rendering BEFORE...")
    print("=" * 60)
    img_before = render_scene(xyz, colors, opacity, scales, quats, camera)
    Image.fromarray(img_before).save(output_dir / "cpu_before.png")
    print("Saved: cpu_before.png")
    
    # Transform
    print("\n" + "=" * 60)
    print(f"Transforming {target['label']}...")
    print("  Translation: [1.5, 1.2, 0.3]")
    print("  Rotation: 45°")
    print("=" * 60)
    
    xyz_new, quats_new = transform_object(
        xyz, quats, target['indices'],
        translation=[1.5, 1.2, 0.3],
        rotation_z_deg=45
    )
    
    # Render AFTER
    print("\n" + "=" * 60)
    print("Rendering AFTER...")
    print("=" * 60)
    img_after = render_scene(xyz_new, colors, opacity, scales, quats_new, camera)
    Image.fromarray(img_after).save(output_dir / "cpu_after.png")
    print("Saved: cpu_after.png")
    
    # Comparison with labels
    comp = np.concatenate([img_before, img_after], axis=1)
    comp_img = Image.fromarray(comp)
    draw = ImageDraw.Draw(comp_img)
    
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
    except:
        font = ImageFont.load_default()
    
    draw.rectangle([(20, 10), (220, 55)], fill=(0, 0, 0, 180))
    draw.text((30, 15), "BEFORE", fill=(255, 255, 255), font=font)
    
    draw.rectangle([(1280 + 20, 10), (1280 + 400, 55)], fill=(0, 0, 0, 180))
    draw.text((1280 + 30, 15), f"AFTER ({target['label']} moved)", fill=(50, 255, 50), font=font)
    
    comp_img.save(output_dir / "cpu_comparison.png")
    print("\nSaved: cpu_comparison.png")
    
    print("\n" + "=" * 60)
    print("DONE!")
    print("=" * 60)


if __name__ == "__main__":
    main()
