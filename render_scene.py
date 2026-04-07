#!/usr/bin/env python3
"""
Full Gaussian Splat Object Manipulation & Rendering
- Proper covariance rotation when rotating objects
- GPU-accelerated rendering via gsplat
"""

import numpy as np
import torch
import json
from plyfile import PlyData, PlyElement
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Tuple
import math


@dataclass
class BoundingBox:
    """Axis-aligned bounding box"""
    min_xyz: np.ndarray
    max_xyz: np.ndarray
    
    @classmethod
    def from_corners(cls, corners: List[dict]) -> 'BoundingBox':
        pts = np.array([[c['x'], c['y'], c['z']] for c in corners])
        return cls(min_xyz=pts.min(axis=0), max_xyz=pts.max(axis=0))
    
    def contains(self, points: np.ndarray, margin: float = 0.0) -> np.ndarray:
        return np.all(
            (points >= self.min_xyz - margin) & 
            (points <= self.max_xyz + margin),
            axis=1
        )
    
    @property
    def center(self) -> np.ndarray:
        return (self.min_xyz + self.max_xyz) / 2


@dataclass
class SceneObject:
    """Segmented object from gaussian splat"""
    ins_id: str
    label: str
    bbox: BoundingBox
    gaussian_indices: np.ndarray
    
    def __repr__(self):
        return f"SceneObject(id={self.ins_id}, label='{self.label}', n={len(self.gaussian_indices)})"


class GaussianScene:
    """Full gaussian splat scene with object manipulation"""
    
    def __init__(self, ply_path: str):
        self.ply_path = Path(ply_path)
        self._load_ply()
        
    def _load_ply(self):
        """Load all gaussian parameters from PLY"""
        ply = PlyData.read(str(self.ply_path))
        v = ply['vertex'].data
        
        # Positions
        self.xyz = np.stack([v['x'], v['y'], v['z']], axis=1).astype(np.float32)
        
        # Spherical harmonics (color)
        # DC component
        self.sh_dc = np.stack([v['f_dc_0'], v['f_dc_1'], v['f_dc_2']], axis=1).astype(np.float32)
        
        # Higher order SH (if present)
        sh_rest = []
        for i in range(45):  # Up to degree 3 SH = 16 coeffs * 3 channels - 3 DC = 45
            try:
                sh_rest.append(v[f'f_rest_{i}'])
            except:
                break
        if sh_rest:
            self.sh_rest = np.stack(sh_rest, axis=1).astype(np.float32)
        else:
            self.sh_rest = np.zeros((len(self.xyz), 0), dtype=np.float32)
        
        # Opacity (logit space in PLY)
        self.opacity_logit = v['opacity'].astype(np.float32)
        
        # Scale (log space in PLY)
        self.scale_log = np.stack([v['scale_0'], v['scale_1'], v['scale_2']], axis=1).astype(np.float32)
        
        # Rotation quaternion (wxyz)
        self.rotation = np.stack([
            v['rot_0'], v['rot_1'], v['rot_2'], v['rot_3']
        ], axis=1).astype(np.float32)
        # Normalize quaternions
        self.rotation = self.rotation / np.linalg.norm(self.rotation, axis=1, keepdims=True)
        
        self.n_gaussians = len(self.xyz)
        print(f"Loaded {self.n_gaussians} gaussians from {self.ply_path.name}")
        print(f"  Position range: {self.xyz.min(axis=0)} to {self.xyz.max(axis=0)}")
        
    def segment_by_labels(self, labels_json: str, margin: float = 0.05) -> List[SceneObject]:
        """Segment scene into objects using label bounding boxes"""
        with open(labels_json) as f:
            labels = json.load(f)
        
        objects = []
        for item in labels:
            if 'bounding_box' not in item:
                continue
            bbox = BoundingBox.from_corners(item['bounding_box'])
            mask = bbox.contains(self.xyz, margin=margin)
            indices = np.where(mask)[0]
            
            if len(indices) > 10:  # Skip tiny fragments
                objects.append(SceneObject(
                    ins_id=item['ins_id'],
                    label=item['label'],
                    bbox=bbox,
                    gaussian_indices=indices
                ))
        
        print(f"Segmented {len(objects)} objects")
        return objects
    
    def transform_object(
        self,
        obj: SceneObject,
        translation: Optional[np.ndarray] = None,
        rotation_deg: Optional[Tuple[float, float, float]] = None,  # Euler XYZ
        pivot: Optional[np.ndarray] = None
    ):
        """
        Apply rigid transform to an object's gaussians.
        Properly transforms both positions and covariance orientations.
        """
        idx = obj.gaussian_indices
        
        if pivot is None:
            pivot = self.xyz[idx].mean(axis=0)
        
        if rotation_deg is not None:
            # Convert euler to quaternion
            rx, ry, rz = [np.radians(a) for a in rotation_deg]
            R = self._euler_to_matrix(rx, ry, rz)
            R_quat = self._matrix_to_quaternion(R)
            
            # Rotate positions around pivot
            centered = self.xyz[idx] - pivot
            rotated = (R @ centered.T).T
            self.xyz[idx] = rotated + pivot
            
            # Rotate gaussian orientations (quaternion multiplication)
            for i, gi in enumerate(idx):
                q_orig = self.rotation[gi]  # wxyz
                q_new = self._quaternion_multiply(R_quat, q_orig)
                self.rotation[gi] = q_new / np.linalg.norm(q_new)
        
        if translation is not None:
            self.xyz[idx] += translation
    
    def _euler_to_matrix(self, rx, ry, rz) -> np.ndarray:
        """Euler XYZ to rotation matrix"""
        cx, sx = np.cos(rx), np.sin(rx)
        cy, sy = np.cos(ry), np.sin(ry)
        cz, sz = np.cos(rz), np.sin(rz)
        
        Rx = np.array([[1,0,0], [0,cx,-sx], [0,sx,cx]])
        Ry = np.array([[cy,0,sy], [0,1,0], [-sy,0,cy]])
        Rz = np.array([[cz,-sz,0], [sz,cz,0], [0,0,1]])
        
        return Rz @ Ry @ Rx
    
    def _matrix_to_quaternion(self, R: np.ndarray) -> np.ndarray:
        """Rotation matrix to quaternion (wxyz)"""
        trace = R[0,0] + R[1,1] + R[2,2]
        
        if trace > 0:
            s = 0.5 / np.sqrt(trace + 1.0)
            w = 0.25 / s
            x = (R[2,1] - R[1,2]) * s
            y = (R[0,2] - R[2,0]) * s
            z = (R[1,0] - R[0,1]) * s
        elif R[0,0] > R[1,1] and R[0,0] > R[2,2]:
            s = 2.0 * np.sqrt(1.0 + R[0,0] - R[1,1] - R[2,2])
            w = (R[2,1] - R[1,2]) / s
            x = 0.25 * s
            y = (R[0,1] + R[1,0]) / s
            z = (R[0,2] + R[2,0]) / s
        elif R[1,1] > R[2,2]:
            s = 2.0 * np.sqrt(1.0 + R[1,1] - R[0,0] - R[2,2])
            w = (R[0,2] - R[2,0]) / s
            x = (R[0,1] + R[1,0]) / s
            y = 0.25 * s
            z = (R[1,2] + R[2,1]) / s
        else:
            s = 2.0 * np.sqrt(1.0 + R[2,2] - R[0,0] - R[1,1])
            w = (R[1,0] - R[0,1]) / s
            x = (R[0,2] + R[2,0]) / s
            y = (R[1,2] + R[2,1]) / s
            z = 0.25 * s
        
        return np.array([w, x, y, z])
    
    def _quaternion_multiply(self, q1, q2) -> np.ndarray:
        """Quaternion multiplication (wxyz format)"""
        w1, x1, y1, z1 = q1
        w2, x2, y2, z2 = q2
        return np.array([
            w1*w2 - x1*x2 - y1*y2 - z1*z2,
            w1*x2 + x1*w2 + y1*z2 - z1*y2,
            w1*y2 - x1*z2 + y1*w2 + z1*x2,
            w1*z2 + x1*y2 - y1*x2 + z1*w2
        ])
    
    def save_ply(self, output_path: str):
        """Save modified scene to PLY"""
        # Reconstruct structured array
        dtype = [
            ('x', 'f4'), ('y', 'f4'), ('z', 'f4'),
            ('nx', 'f4'), ('ny', 'f4'), ('nz', 'f4'),
            ('f_dc_0', 'f4'), ('f_dc_1', 'f4'), ('f_dc_2', 'f4'),
        ]
        
        # Add SH rest
        for i in range(self.sh_rest.shape[1]):
            dtype.append((f'f_rest_{i}', 'f4'))
        
        dtype.extend([
            ('opacity', 'f4'),
            ('scale_0', 'f4'), ('scale_1', 'f4'), ('scale_2', 'f4'),
            ('rot_0', 'f4'), ('rot_1', 'f4'), ('rot_2', 'f4'), ('rot_3', 'f4'),
        ])
        
        data = np.zeros(self.n_gaussians, dtype=dtype)
        data['x'] = self.xyz[:, 0]
        data['y'] = self.xyz[:, 1]
        data['z'] = self.xyz[:, 2]
        data['nx'] = 0
        data['ny'] = 0
        data['nz'] = 0
        data['f_dc_0'] = self.sh_dc[:, 0]
        data['f_dc_1'] = self.sh_dc[:, 1]
        data['f_dc_2'] = self.sh_dc[:, 2]
        
        for i in range(self.sh_rest.shape[1]):
            data[f'f_rest_{i}'] = self.sh_rest[:, i]
        
        data['opacity'] = self.opacity_logit
        data['scale_0'] = self.scale_log[:, 0]
        data['scale_1'] = self.scale_log[:, 1]
        data['scale_2'] = self.scale_log[:, 2]
        data['rot_0'] = self.rotation[:, 0]
        data['rot_1'] = self.rotation[:, 1]
        data['rot_2'] = self.rotation[:, 2]
        data['rot_3'] = self.rotation[:, 3]
        
        el = PlyElement.describe(data, 'vertex')
        PlyData([el]).write(output_path)
        print(f"Saved to {output_path}")


def render_gaussian_splat(
    scene: GaussianScene,
    camera_position: np.ndarray,
    look_at: np.ndarray,
    width: int = 800,
    height: int = 600,
    fov: float = 60.0,
    background: Tuple[float, float, float] = (1.0, 1.0, 1.0)
) -> np.ndarray:
    """
    Render gaussian splat from a camera viewpoint using gsplat.
    Returns RGB image as numpy array.
    """
    import gsplat
    from gsplat import rasterization
    
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Rendering on {device}...")
    
    # Camera setup
    up = np.array([0, 0, 1], dtype=np.float32)
    forward = look_at - camera_position
    forward = forward / np.linalg.norm(forward)
    right = np.cross(forward, up)
    right = right / np.linalg.norm(right)
    up = np.cross(right, forward)
    
    # View matrix (world to camera)
    R = np.stack([right, -up, forward], axis=0)  # 3x3
    t = -R @ camera_position
    
    # Intrinsics
    fx = fy = width / (2 * np.tan(np.radians(fov / 2)))
    cx, cy = width / 2, height / 2
    
    # Convert to torch tensors
    means = torch.from_numpy(scene.xyz).float().to(device)
    quats = torch.from_numpy(scene.rotation).float().to(device)
    scales = torch.from_numpy(np.exp(scene.scale_log)).float().to(device)
    opacities = torch.from_numpy(1 / (1 + np.exp(-scene.opacity_logit))).float().to(device)
    
    # Colors from SH DC (simplified - just use DC term)
    # SH to RGB: color = SH_C0 * dc + 0.5
    SH_C0 = 0.28209479177387814
    colors = torch.from_numpy(scene.sh_dc * SH_C0 + 0.5).float().clamp(0, 1).to(device)
    
    # Camera tensors
    viewmat = torch.eye(4, device=device)
    viewmat[:3, :3] = torch.from_numpy(R).float()
    viewmat[:3, 3] = torch.from_numpy(t).float()
    
    K = torch.tensor([
        [fx, 0, cx],
        [0, fy, cy],
        [0, 0, 1]
    ], device=device, dtype=torch.float32)
    
    # Render
    try:
        renders, alphas, meta = rasterization(
            means=means,
            quats=quats,
            scales=scales,
            opacities=opacities,
            colors=colors,
            viewmats=viewmat.unsqueeze(0),
            Ks=K.unsqueeze(0),
            width=width,
            height=height,
            near_plane=0.1,
            far_plane=100.0,
            backgrounds=torch.tensor([background], device=device, dtype=torch.float32),
        )
        
        image = renders[0].cpu().numpy()
        image = np.clip(image, 0, 1)
        return (image * 255).astype(np.uint8)
        
    except Exception as e:
        print(f"gsplat render failed: {e}")
        print("Falling back to point cloud visualization...")
        return render_pointcloud_fallback(scene, camera_position, look_at, width, height, fov)


def render_pointcloud_fallback(
    scene: GaussianScene,
    camera_position: np.ndarray,
    look_at: np.ndarray,
    width: int,
    height: int,
    fov: float
) -> np.ndarray:
    """Fallback: render as colored point cloud"""
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d import Axes3D
    from io import BytesIO
    from PIL import Image
    
    # Subsample for speed
    n_sample = min(100000, scene.n_gaussians)
    idx = np.random.choice(scene.n_gaussians, n_sample, replace=False)
    
    # Colors from SH
    SH_C0 = 0.28209479177387814
    colors = np.clip(scene.sh_dc[idx] * SH_C0 + 0.5, 0, 1)
    
    fig = plt.figure(figsize=(width/100, height/100), dpi=100)
    ax = fig.add_subplot(111, projection='3d')
    
    ax.scatter(
        scene.xyz[idx, 0],
        scene.xyz[idx, 1],
        scene.xyz[idx, 2],
        c=colors,
        s=0.1,
        alpha=0.8
    )
    
    # Set camera
    ax.view_init(elev=20, azim=45)
    ax.set_xlim(scene.xyz[:, 0].min(), scene.xyz[:, 0].max())
    ax.set_ylim(scene.xyz[:, 1].min(), scene.xyz[:, 1].max())
    ax.set_zlim(scene.xyz[:, 2].min(), scene.xyz[:, 2].max())
    ax.axis('off')
    
    # Convert to image
    buf = BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', pad_inches=0)
    plt.close()
    buf.seek(0)
    img = np.array(Image.open(buf))[:, :, :3]
    
    return img


def main():
    # Paths
    splat_path = "/home/ubuntu/datasets/sage_batch_walking/0001_839920_seq_006_fruit/gs_output_15k/point_cloud/iteration_15000/point_cloud.ply"
    labels_path = "/home/ubuntu/datasets/scenes/0001_839920/labels.json"
    output_dir = Path("/home/ubuntu/.openclaw/workspace")
    
    # Load scene
    print("=" * 60)
    print("Loading Gaussian Splat Scene")
    print("=" * 60)
    scene = GaussianScene(splat_path)
    
    # Segment objects
    print("\n" + "=" * 60)
    print("Segmenting Objects")
    print("=" * 60)
    objects = scene.segment_by_labels(labels_path, margin=0.05)
    
    # Print object summary
    from collections import Counter
    label_counts = Counter(o.label for o in objects)
    print("\nObjects found:")
    for label, count in label_counts.most_common(15):
        samples = [o for o in objects if o.label == label]
        total_pts = sum(len(o.gaussian_indices) for o in samples)
        print(f"  {label}: {count} instances, {total_pts} total gaussians")
    
    # Find a good object to manipulate (chair or table)
    target = None
    for label in ['chair', 'table', 'Multi person sofa', 'plant']:
        candidates = [o for o in objects if o.label == label and len(o.gaussian_indices) > 5000]
        if candidates:
            target = max(candidates, key=lambda o: len(o.gaussian_indices))
            break
    
    if target is None:
        target = max(objects, key=lambda o: len(o.gaussian_indices))
    
    print(f"\n{'=' * 60}")
    print(f"Selected object to manipulate: {target}")
    print(f"{'=' * 60}")
    
    # Camera setup - find a good viewpoint
    scene_center = scene.xyz.mean(axis=0)
    scene_extent = scene.xyz.max(axis=0) - scene.xyz.min(axis=0)
    camera_distance = max(scene_extent) * 1.5
    
    # Position camera looking at the target object
    obj_center = scene.xyz[target.gaussian_indices].mean(axis=0)
    camera_pos = obj_center + np.array([camera_distance * 0.5, -camera_distance * 0.5, camera_distance * 0.3])
    
    print(f"\nCamera at {camera_pos}, looking at {obj_center}")
    
    # Render BEFORE
    print("\n" + "=" * 60)
    print("Rendering ORIGINAL scene...")
    print("=" * 60)
    img_before = render_gaussian_splat(
        scene, 
        camera_pos, 
        obj_center,
        width=1024,
        height=768
    )
    
    from PIL import Image
    Image.fromarray(img_before).save(output_dir / "render_before.png")
    print(f"Saved: {output_dir / 'render_before.png'}")
    
    # Transform the object
    print(f"\n{'=' * 60}")
    print(f"Transforming {target.label}...")
    print(f"  - Translation: [1.0, 0.5, 0.3]")
    print(f"  - Rotation: 45° around Z axis")
    print(f"{'=' * 60}")
    
    original_center = scene.xyz[target.gaussian_indices].mean(axis=0).copy()
    
    scene.transform_object(
        target,
        translation=np.array([1.0, 0.5, 0.3]),
        rotation_deg=(0, 0, 45)  # 45 degrees around Z
    )
    
    new_center = scene.xyz[target.gaussian_indices].mean(axis=0)
    print(f"  Original center: {original_center}")
    print(f"  New center: {new_center}")
    
    # Render AFTER
    print("\n" + "=" * 60)
    print("Rendering MODIFIED scene...")
    print("=" * 60)
    img_after = render_gaussian_splat(
        scene,
        camera_pos,
        obj_center,  # Keep looking at original position to show the move
        width=1024,
        height=768
    )
    
    Image.fromarray(img_after).save(output_dir / "render_after.png")
    print(f"Saved: {output_dir / 'render_after.png'}")
    
    # Create side-by-side comparison
    comparison = np.concatenate([img_before, img_after], axis=1)
    Image.fromarray(comparison).save(output_dir / "render_comparison.png")
    print(f"Saved: {output_dir / 'render_comparison.png'}")
    
    # Save modified PLY
    scene.save_ply(str(output_dir / "modified_scene.ply"))
    
    print("\n" + "=" * 60)
    print("DONE!")
    print("=" * 60)
    print(f"\nOutput files:")
    print(f"  - render_before.png: Original scene")
    print(f"  - render_after.png: Scene with {target.label} moved + rotated")
    print(f"  - render_comparison.png: Side-by-side")
    print(f"  - modified_scene.ply: Full modified gaussian splat")


if __name__ == "__main__":
    main()
