#!/usr/bin/env python3
"""
Software Gaussian Splat Renderer
Proper 2D gaussian splatting without GPU acceleration (for visualization)
"""

import numpy as np
from PIL import Image
from plyfile import PlyData
from dataclasses import dataclass
from typing import Tuple, Optional
import json
from collections import Counter


@dataclass
class Camera:
    """Pinhole camera model"""
    position: np.ndarray  # [3]
    look_at: np.ndarray   # [3]
    up: np.ndarray = None
    fov_deg: float = 60.0
    width: int = 1024
    height: int = 768
    
    def __post_init__(self):
        if self.up is None:
            self.up = np.array([0, 0, 1], dtype=np.float32)
        
        # Compute camera basis
        self.forward = self.look_at - self.position
        self.forward = self.forward / np.linalg.norm(self.forward)
        
        self.right = np.cross(self.forward, self.up)
        self.right = self.right / np.linalg.norm(self.right)
        
        self.true_up = np.cross(self.right, self.forward)
        
        # Intrinsics
        self.fx = self.width / (2 * np.tan(np.radians(self.fov_deg / 2)))
        self.fy = self.fx
        self.cx = self.width / 2
        self.cy = self.height / 2
    
    def world_to_camera(self, points: np.ndarray) -> np.ndarray:
        """Transform world points to camera space"""
        # Translate to camera origin
        p = points - self.position
        # Rotate to camera frame
        return np.stack([
            np.dot(p, self.right),
            np.dot(p, -self.true_up),  # Y down in image
            np.dot(p, self.forward)
        ], axis=-1)
    
    def project(self, points_cam: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Project camera-space points to 2D. Returns (uv, depth)"""
        z = points_cam[..., 2]
        valid = z > 0.1  # Near plane
        
        u = np.where(valid, self.fx * points_cam[..., 0] / z + self.cx, -1)
        v = np.where(valid, self.fy * points_cam[..., 1] / z + self.cy, -1)
        
        return np.stack([u, v], axis=-1), z, valid


def quaternion_to_matrix(q: np.ndarray) -> np.ndarray:
    """Convert quaternion (wxyz) to rotation matrix"""
    w, x, y, z = q
    return np.array([
        [1 - 2*(y*y + z*z), 2*(x*y - w*z), 2*(x*z + w*y)],
        [2*(x*y + w*z), 1 - 2*(x*x + z*z), 2*(y*z - w*x)],
        [2*(x*z - w*y), 2*(y*z + w*x), 1 - 2*(x*x + y*y)]
    ])


def compute_cov2d(
    cov3d: np.ndarray,  # [3, 3]
    mean_cam: np.ndarray,  # [3]
    fx: float,
    fy: float
) -> np.ndarray:
    """Project 3D covariance to 2D screen space"""
    # Jacobian of projection at this point
    z = mean_cam[2]
    z2 = z * z
    
    J = np.array([
        [fx / z, 0, -fx * mean_cam[0] / z2],
        [0, fy / z, -fy * mean_cam[1] / z2]
    ])
    
    # Project covariance
    cov2d = J @ cov3d @ J.T
    
    # Add small regularization for numerical stability
    cov2d[0, 0] += 0.3
    cov2d[1, 1] += 0.3
    
    return cov2d


def render_gaussians_software(
    means: np.ndarray,      # [N, 3] positions
    quats: np.ndarray,      # [N, 4] wxyz quaternions
    scales: np.ndarray,     # [N, 3] scales (already exp'd)
    opacities: np.ndarray,  # [N] sigmoid'd opacities
    colors: np.ndarray,     # [N, 3] RGB colors
    camera: Camera,
    background: Tuple[float, float, float] = (1.0, 1.0, 1.0)
) -> np.ndarray:
    """
    Software gaussian splat rendering.
    Returns RGB image as uint8 numpy array.
    """
    n_gaussians = len(means)
    print(f"Rendering {n_gaussians} gaussians...")
    
    # Transform to camera space
    means_cam = camera.world_to_camera(means)
    
    # Project to screen
    uv, depth, valid = camera.project(means_cam)
    
    # Filter visible gaussians
    visible_mask = valid & (uv[:, 0] >= -100) & (uv[:, 0] < camera.width + 100)
    visible_mask &= (uv[:, 1] >= -100) & (uv[:, 1] < camera.height + 100)
    visible_mask &= (depth < 50)  # Far plane
    
    visible_indices = np.where(visible_mask)[0]
    print(f"  {len(visible_indices)} gaussians in view")
    
    if len(visible_indices) == 0:
        return (np.ones((camera.height, camera.width, 3)) * 255 * np.array(background)).astype(np.uint8)
    
    # Sort by depth (back to front for alpha blending)
    sorted_indices = visible_indices[np.argsort(-depth[visible_indices])]
    
    # Initialize output
    image = np.ones((camera.height, camera.width, 3), dtype=np.float32) * np.array(background)
    alpha_acc = np.zeros((camera.height, camera.width), dtype=np.float32)
    
    # Render each gaussian (this is slow but correct)
    # For speed, we only render a subset
    max_render = min(50000, len(sorted_indices))
    sorted_indices = sorted_indices[:max_render]
    
    print(f"  Rendering {len(sorted_indices)} gaussians...")
    
    for i, idx in enumerate(sorted_indices):
        if i % 10000 == 0:
            print(f"    {i}/{len(sorted_indices)}")
        
        # Get gaussian params
        mean_cam = means_cam[idx]
        scale = scales[idx]
        quat = quats[idx]
        opacity = opacities[idx]
        color = colors[idx]
        
        # Build 3D covariance from scale and rotation
        R = quaternion_to_matrix(quat)
        S = np.diag(scale ** 2)
        cov3d = R @ S @ R.T
        
        # Project to 2D
        try:
            cov2d = compute_cov2d(cov3d, mean_cam, camera.fx, camera.fy)
        except:
            continue
        
        # Get screen position
        u, v = uv[idx]
        
        # Compute eigendecomposition for efficient rendering
        det = cov2d[0, 0] * cov2d[1, 1] - cov2d[0, 1] * cov2d[1, 0]
        if det <= 0:
            continue
        
        inv_cov = np.array([
            [cov2d[1, 1], -cov2d[0, 1]],
            [-cov2d[1, 0], cov2d[0, 0]]
        ]) / det
        
        # Compute radius (3 sigma)
        eigenvalues = np.linalg.eigvalsh(cov2d)
        radius = int(np.ceil(3 * np.sqrt(max(eigenvalues)))) + 1
        radius = min(radius, 50)  # Cap for sanity
        
        # Render gaussian to local patch
        x_min = max(0, int(u) - radius)
        x_max = min(camera.width, int(u) + radius + 1)
        y_min = max(0, int(v) - radius)
        y_max = min(camera.height, int(v) + radius + 1)
        
        if x_max <= x_min or y_max <= y_min:
            continue
        
        # Create coordinate grid
        xs = np.arange(x_min, x_max) - u
        ys = np.arange(y_min, y_max) - v
        xx, yy = np.meshgrid(xs, ys)
        
        # Evaluate gaussian
        d = np.stack([xx, yy], axis=-1)  # [H, W, 2]
        mahal = np.sum(d @ inv_cov * d, axis=-1)  # Mahalanobis distance
        
        gauss = np.exp(-0.5 * mahal) * opacity
        
        # Alpha blend
        alpha_here = gauss * (1 - alpha_acc[y_min:y_max, x_min:x_max])
        
        for c in range(3):
            image[y_min:y_max, x_min:x_max, c] += alpha_here * color[c]
        
        alpha_acc[y_min:y_max, x_min:x_max] += alpha_here
    
    # Normalize by accumulated alpha
    image = np.clip(image, 0, 1)
    
    return (image * 255).astype(np.uint8)


class GaussianScene:
    """Load and manipulate gaussian splat scene"""
    
    def __init__(self, ply_path: str):
        ply = PlyData.read(ply_path)
        v = ply['vertex'].data
        
        self.xyz = np.stack([v['x'], v['y'], v['z']], axis=1).astype(np.float32)
        self.sh_dc = np.stack([v['f_dc_0'], v['f_dc_1'], v['f_dc_2']], axis=1).astype(np.float32)
        self.opacity_logit = v['opacity'].astype(np.float32)
        self.scale_log = np.stack([v['scale_0'], v['scale_1'], v['scale_2']], axis=1).astype(np.float32)
        self.rotation = np.stack([v['rot_0'], v['rot_1'], v['rot_2'], v['rot_3']], axis=1).astype(np.float32)
        self.rotation = self.rotation / np.linalg.norm(self.rotation, axis=1, keepdims=True)
        
        print(f"Loaded {len(self.xyz)} gaussians")
        print(f"  Range: {self.xyz.min(axis=0)} to {self.xyz.max(axis=0)}")
    
    def get_render_params(self):
        """Get parameters ready for rendering"""
        # Convert SH to RGB
        SH_C0 = 0.28209479177387814
        colors = np.clip(self.sh_dc * SH_C0 + 0.5, 0, 1)
        
        # Convert scales and opacities
        scales = np.exp(self.scale_log)
        opacities = 1 / (1 + np.exp(-self.opacity_logit))
        
        return self.xyz, self.rotation, scales, opacities, colors
    
    def segment_objects(self, labels_json: str, margin: float = 0.05):
        """Segment by bounding boxes"""
        with open(labels_json) as f:
            labels = json.load(f)
        
        objects = []
        for item in labels:
            if 'bounding_box' not in item:
                continue
            pts = np.array([[c['x'], c['y'], c['z']] for c in item['bounding_box']])
            bbox_min, bbox_max = pts.min(axis=0), pts.max(axis=0)
            
            mask = np.all((self.xyz >= bbox_min - margin) & (self.xyz <= bbox_max + margin), axis=1)
            indices = np.where(mask)[0]
            
            if len(indices) > 10:
                objects.append({
                    'id': item['ins_id'],
                    'label': item['label'],
                    'indices': indices,
                    'center': (bbox_min + bbox_max) / 2
                })
        
        return objects
    
    def transform_object(self, indices: np.ndarray, translation: np.ndarray, rotation_z_deg: float = 0):
        """Move and rotate an object"""
        pivot = self.xyz[indices].mean(axis=0)
        
        if rotation_z_deg != 0:
            theta = np.radians(rotation_z_deg)
            c, s = np.cos(theta), np.sin(theta)
            R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
            
            # Rotate positions
            centered = self.xyz[indices] - pivot
            self.xyz[indices] = (R @ centered.T).T + pivot
            
            # Rotate quaternions
            R_quat = np.array([np.cos(theta/2), 0, 0, np.sin(theta/2)])  # wxyz
            for i in indices:
                q = self.rotation[i]
                # Quaternion multiply
                w1, x1, y1, z1 = R_quat
                w2, x2, y2, z2 = q
                self.rotation[i] = np.array([
                    w1*w2 - x1*x2 - y1*y2 - z1*z2,
                    w1*x2 + x1*w2 + y1*z2 - z1*y2,
                    w1*y2 - x1*z2 + y1*w2 + z1*x2,
                    w1*z2 + x1*y2 - y1*x2 + z1*w2
                ])
                self.rotation[i] /= np.linalg.norm(self.rotation[i])
        
        self.xyz[indices] += translation


def main():
    # Paths
    ply_path = "/home/ubuntu/datasets/sage_batch_walking/0001_839920_seq_006_fruit/gs_output_15k/point_cloud/iteration_15000/point_cloud.ply"
    labels_path = "/home/ubuntu/datasets/scenes/0001_839920/labels.json"
    
    print("=" * 60)
    print("Loading scene...")
    print("=" * 60)
    scene = GaussianScene(ply_path)
    
    # Segment objects
    print("\nSegmenting objects...")
    objects = scene.segment_objects(labels_path)
    
    label_counts = Counter(o['label'] for o in objects)
    print("\nFound objects:")
    for label, count in label_counts.most_common(10):
        n_pts = sum(len(o['indices']) for o in objects if o['label'] == label)
        print(f"  {label}: {count} ({n_pts} pts)")
    
    # Pick object to move
    target = None
    for label in ['chair', 'Multi person sofa', 'table']:
        candidates = [o for o in objects if o['label'] == label and len(o['indices']) > 3000]
        if candidates:
            target = max(candidates, key=lambda o: len(o['indices']))
            break
    
    if not target:
        target = max(objects, key=lambda o: len(o['indices']))
    
    print(f"\nSelected: {target['label']} with {len(target['indices'])} gaussians")
    
    # Camera setup - looking at the object
    obj_center = scene.xyz[target['indices']].mean(axis=0)
    scene_center = scene.xyz.mean(axis=0)
    
    # Position camera to see the scene with object
    cam_offset = np.array([4, -4, 3])
    camera = Camera(
        position=obj_center + cam_offset,
        look_at=obj_center,
        width=1024,
        height=768,
        fov_deg=50
    )
    
    print(f"\nCamera at {camera.position}, looking at {camera.look_at}")
    
    # Render BEFORE
    print("\n" + "=" * 60)
    print("Rendering BEFORE...")
    print("=" * 60)
    means, quats, scales, opacities, colors = scene.get_render_params()
    
    img_before = render_gaussians_software(
        means, quats, scales, opacities, colors, camera
    )
    Image.fromarray(img_before).save("/home/ubuntu/.openclaw/workspace/gs_before.png")
    print("Saved: gs_before.png")
    
    # Transform object
    print(f"\n{'=' * 60}")
    print(f"Moving {target['label']}...")
    print(f"  Translation: [1.5, 1.0, 0.5]")
    print(f"  Rotation: 45°")
    print("=" * 60)
    
    scene.transform_object(
        target['indices'],
        translation=np.array([1.5, 1.0, 0.5]),
        rotation_z_deg=45
    )
    
    # Render AFTER
    print("\n" + "=" * 60)
    print("Rendering AFTER...")
    print("=" * 60)
    means, quats, scales, opacities, colors = scene.get_render_params()
    
    img_after = render_gaussians_software(
        means, quats, scales, opacities, colors, camera
    )
    Image.fromarray(img_after).save("/home/ubuntu/.openclaw/workspace/gs_after.png")
    print("Saved: gs_after.png")
    
    # Side by side
    comparison = np.concatenate([img_before, img_after], axis=1)
    Image.fromarray(comparison).save("/home/ubuntu/.openclaw/workspace/gs_comparison.png")
    print("Saved: gs_comparison.png")
    
    print("\n" + "=" * 60)
    print("DONE!")
    print("=" * 60)


if __name__ == "__main__":
    main()
