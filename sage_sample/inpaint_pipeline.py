#!/usr/bin/env python3
"""
Gaussian Splatting Inpainting Pipeline

This pipeline:
1. Renders a gaussian splat from synthetic camera views
2. Generates masks for object removal or completion regions
3. Applies 2D inpainting (LaMa or Stable Diffusion)
4. Optimizes gaussians to match inpainted views

Works with your existing gaussian_splatting conda env.
"""

import os
import sys
import json
import math
import numpy as np
import torch
import torch.nn.functional as F
from pathlib import Path
from typing import Optional, List, Tuple
from dataclasses import dataclass
from PIL import Image
import argparse

# Use the working diff_gaussian_rasterization
import diff_gaussian_rasterization as dgr

# For PLY reading
from plyfile import PlyData

@dataclass
class Camera:
    """Camera parameters"""
    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int
    c2w: np.ndarray  # camera-to-world 4x4 matrix
    
    @property
    def w2c(self):
        return np.linalg.inv(self.c2w)
    
    @property
    def K(self):
        return np.array([
            [self.fx, 0, self.cx],
            [0, self.fy, self.cy],
            [0, 0, 1]
        ])


def load_gaussians(ply_path: str, filter_invalid: bool = True) -> dict:
    """Load gaussian splat from PLY file"""
    plydata = PlyData.read(ply_path)
    vertex = plydata['vertex']
    
    # Get indices of valid gaussians (filter inf/nan in opacity)
    opacity_raw = vertex['opacity']
    if filter_invalid:
        valid_mask = ~np.isinf(opacity_raw) & ~np.isnan(opacity_raw)
        print(f"Filtering: {valid_mask.sum()} of {len(opacity_raw)} gaussians valid")
    else:
        valid_mask = np.ones(len(opacity_raw), dtype=bool)
    
    # Positions
    xyz = np.stack([vertex['x'][valid_mask], vertex['y'][valid_mask], vertex['z'][valid_mask]], axis=-1)
    
    # Opacity (stored as sigmoid inverse)
    opacity = vertex['opacity'][valid_mask]
    
    # Spherical harmonics for color
    dc = np.stack([vertex['f_dc_0'][valid_mask], vertex['f_dc_1'][valid_mask], vertex['f_dc_2'][valid_mask]], axis=-1)
    
    # Get SH coefficients if present
    sh_rest = []
    vertex_names = vertex.data.dtype.names
    for i in range(45):  # f_rest_0 to f_rest_44
        key = f'f_rest_{i}'
        if key in vertex_names:
            sh_rest.append(vertex[key][valid_mask])
    if sh_rest:
        sh_rest = np.stack(sh_rest, axis=-1)
    else:
        sh_rest = None
    
    # Scale (log scale)
    scale = np.stack([vertex['scale_0'][valid_mask], vertex['scale_1'][valid_mask], vertex['scale_2'][valid_mask]], axis=-1)
    
    # Rotation (quaternion)
    rot = np.stack([vertex['rot_0'][valid_mask], vertex['rot_1'][valid_mask], vertex['rot_2'][valid_mask], vertex['rot_3'][valid_mask]], axis=-1)
    
    return {
        'xyz': torch.tensor(xyz, dtype=torch.float32),
        'opacity': torch.tensor(opacity, dtype=torch.float32),
        'dc': torch.tensor(dc, dtype=torch.float32),
        'sh_rest': torch.tensor(sh_rest, dtype=torch.float32) if sh_rest is not None else None,
        'scale': torch.tensor(scale, dtype=torch.float32),
        'rotation': torch.tensor(rot, dtype=torch.float32),
    }


def generate_orbit_cameras(
    center: np.ndarray,
    radius: float,
    elevation: float,
    n_views: int,
    width: int = 512,
    height: int = 512,
    fov: float = 60.0
) -> List[Camera]:
    """Generate cameras in an orbit around a center point"""
    cameras = []
    
    # Intrinsics from FOV
    fx = fy = width / (2 * np.tan(np.radians(fov / 2)))
    cx, cy = width / 2, height / 2
    
    for i in range(n_views):
        azimuth = 2 * np.pi * i / n_views
        
        # Camera position
        x = center[0] + radius * np.cos(azimuth) * np.cos(elevation)
        y = center[1] + radius * np.sin(elevation)
        z = center[2] + radius * np.sin(azimuth) * np.cos(elevation)
        cam_pos = np.array([x, y, z])
        
        # Look at center
        forward = center - cam_pos
        forward = forward / np.linalg.norm(forward)
        
        # Up vector (world Y)
        up = np.array([0, 1, 0])
        right = np.cross(forward, up)
        right = right / np.linalg.norm(right)
        up = np.cross(right, forward)
        
        # Camera-to-world matrix
        c2w = np.eye(4)
        c2w[:3, 0] = right
        c2w[:3, 1] = up
        c2w[:3, 2] = -forward  # OpenGL convention
        c2w[:3, 3] = cam_pos
        
        cameras.append(Camera(
            fx=fx, fy=fy, cx=cx, cy=cy,
            width=width, height=height, c2w=c2w
        ))
    
    return cameras


def render_gaussians(
    gaussians: dict,
    camera: Camera,
    device: str = 'cuda',
    bg_color: torch.Tensor = None
) -> Tuple[torch.Tensor, torch.Tensor]:
    """Render gaussian splat from a camera viewpoint using diff_gaussian_rasterization
    
    Returns:
        rgb: [H, W, 3] tensor
        depth: [H, W] tensor
    """
    if bg_color is None:
        bg_color = torch.tensor([1.0, 1.0, 1.0], dtype=torch.float32, device=device)
    
    # Move to device
    means3D = gaussians['xyz'].to(device)
    opacity = torch.sigmoid(gaussians['opacity'].to(device)).unsqueeze(-1)
    
    # Scale and rotation
    scales = torch.exp(gaussians['scale'].to(device))
    rotations = F.normalize(gaussians['rotation'].to(device), dim=-1)
    
    # SH DC coefficients
    sh0 = gaussians['dc'].to(device)
    shs = sh0.unsqueeze(1)  # [N, 1, 3]
    
    # Build W2C from camera C2W
    # Note: 3DGS expects camera looking down +Z, so we need forward in +Z column
    c2w = camera.c2w.copy()
    # Flip the forward vector (col 2) to go from -Z to +Z convention
    c2w[:3, 2] = -c2w[:3, 2]
    
    w2c = np.linalg.inv(c2w)
    world_view_transform = torch.tensor(w2c.T, dtype=torch.float32, device=device)
    
    # Camera center
    cam_center = world_view_transform.inverse()[3, :3]
    
    # Projection matrix (following 3DGS convention)
    znear, zfar = 0.01, 100.0
    fovx = 2 * math.atan(camera.width / (2 * camera.fx))
    fovy = 2 * math.atan(camera.height / (2 * camera.fy))
    
    tanHalfFovY = math.tan(fovy / 2)
    tanHalfFovX = math.tan(fovx / 2)
    
    top = tanHalfFovY * znear
    bottom = -top
    right = tanHalfFovX * znear
    left = -right
    
    P = torch.zeros(4, 4, device=device)
    z_sign = 1.0
    P[0, 0] = 2.0 * znear / (right - left)
    P[1, 1] = 2.0 * znear / (top - bottom)
    P[0, 2] = (right + left) / (right - left)
    P[1, 2] = (top + bottom) / (top - bottom)
    P[3, 2] = z_sign
    P[2, 2] = z_sign * zfar / (zfar - znear)
    P[2, 3] = -(zfar * znear) / (zfar - znear)
    
    # Transpose projection matrix
    proj_matrix = P.T
    
    # Full projection
    full_proj_transform = world_view_transform.unsqueeze(0).bmm(proj_matrix.unsqueeze(0)).squeeze(0)
    
    # Rasterizer settings
    raster_settings = dgr.GaussianRasterizationSettings(
        image_height=camera.height,
        image_width=camera.width,
        tanfovx=tanHalfFovX,
        tanfovy=tanHalfFovY,
        bg=bg_color,
        scale_modifier=1.0,
        viewmatrix=world_view_transform,
        projmatrix=full_proj_transform,
        sh_degree=0,  # Just DC
        campos=cam_center,
        prefiltered=False,
        debug=False,
        antialiasing=False,
    )
    
    rasterizer = dgr.GaussianRasterizer(raster_settings=raster_settings)
    
    # Rasterize
    rendered, radii, invdepths = rasterizer(
        means3D=means3D,
        means2D=torch.zeros_like(means3D[:, :2]),  # Will be computed
        shs=shs,
        colors_precomp=None,
        opacities=opacity,
        scales=scales,
        rotations=rotations,
        cov3D_precomp=None,
    )
    
    # rendered is [3, H, W], convert to [H, W, 3]
    rgb = rendered.permute(1, 2, 0)
    
    # Convert invdepths to depth (invdepths is [1, H, W])
    depth = invdepths.squeeze(0)
    
    return rgb, depth


def create_object_mask(
    gaussians: dict,
    camera: Camera,
    object_indices: np.ndarray,
    device: str = 'cuda'
) -> torch.Tensor:
    """Create a mask for specific gaussians (the object to remove)
    
    Returns:
        mask: [H, W] binary tensor (1 = object, 0 = background)
    """
    # Create a copy with only the object gaussians visible
    object_gaussians = {
        'xyz': gaussians['xyz'][object_indices],
        'opacity': gaussians['opacity'][object_indices],
        'dc': torch.ones_like(gaussians['dc'][object_indices]),  # White color
        'sh_rest': None,
        'scale': gaussians['scale'][object_indices],
        'rotation': gaussians['rotation'][object_indices],
    }
    
    rgb, _ = render_gaussians(object_gaussians, camera, device)
    
    # Convert to mask (any non-zero is part of object)
    mask = (rgb.sum(dim=-1) > 0.1).float()
    
    # Dilate mask slightly to cover edges
    mask = mask.unsqueeze(0).unsqueeze(0)
    kernel_size = 5
    mask = F.max_pool2d(mask, kernel_size, stride=1, padding=kernel_size//2)
    mask = mask.squeeze()
    
    return mask


def inpaint_with_lama(
    image: np.ndarray,
    mask: np.ndarray,
    lama_path: str = '/home/ubuntu/projects/lama/big-lama/big-lama'
) -> np.ndarray:
    """Inpaint using LaMa model
    
    Args:
        image: [H, W, 3] RGB image (0-255)
        mask: [H, W] binary mask (255 = inpaint region)
        lama_path: path to LaMa checkpoint directory
    
    Returns:
        inpainted: [H, W, 3] RGB image
    """
    # TODO: Implement LaMa inference
    # For now, use diffusers inpainting as it's already installed
    from diffusers import StableDiffusionInpaintPipeline
    
    pipe = StableDiffusionInpaintPipeline.from_pretrained(
        "runwayml/stable-diffusion-inpainting",
        torch_dtype=torch.float16,
    ).to("cuda")
    
    # Convert to PIL
    image_pil = Image.fromarray(image)
    mask_pil = Image.fromarray(mask)
    
    # Resize to SD dimensions
    orig_size = image_pil.size
    image_pil = image_pil.resize((512, 512))
    mask_pil = mask_pil.resize((512, 512))
    
    # Inpaint
    result = pipe(
        prompt="clean floor, indoor scene",
        image=image_pil,
        mask_image=mask_pil,
        num_inference_steps=30,
    ).images[0]
    
    # Resize back
    result = result.resize(orig_size)
    
    return np.array(result)


def optimize_gaussians(
    gaussians: dict,
    cameras: List[Camera],
    target_images: List[np.ndarray],
    masks: List[np.ndarray],
    n_iters: int = 1000,
    lr: float = 0.01,
    device: str = 'cuda'
) -> dict:
    """Optimize gaussian parameters to match target (inpainted) images
    
    Only optimizes gaussians in the masked regions.
    """
    # Make parameters trainable
    means = gaussians['xyz'].clone().to(device).requires_grad_(True)
    opacities = gaussians['opacity'].clone().to(device).requires_grad_(True)
    dc = gaussians['dc'].clone().to(device).requires_grad_(True)
    scales = gaussians['scale'].clone().to(device).requires_grad_(True)
    rotations = gaussians['rotation'].clone().to(device).requires_grad_(True)
    
    optimizer = torch.optim.Adam([
        {'params': means, 'lr': lr},
        {'params': opacities, 'lr': lr * 0.1},
        {'params': dc, 'lr': lr},
        {'params': scales, 'lr': lr * 0.1},
        {'params': rotations, 'lr': lr * 0.1},
    ])
    
    # Convert targets to tensors
    targets = [torch.tensor(img / 255.0, dtype=torch.float32, device=device) 
               for img in target_images]
    mask_tensors = [torch.tensor(m / 255.0, dtype=torch.float32, device=device) 
                    for m in masks]
    
    for iteration in range(n_iters):
        optimizer.zero_grad()
        total_loss = 0
        
        for cam, target, mask in zip(cameras, targets, mask_tensors):
            # Create gaussians dict for rendering
            g = {
                'xyz': means,
                'opacity': opacities,
                'dc': dc,
                'sh_rest': None,
                'scale': scales,
                'rotation': rotations,
            }
            
            rgb, _ = render_gaussians(g, cam, device)
            
            # Loss only in masked region
            loss = ((rgb - target) ** 2 * mask.unsqueeze(-1)).mean()
            total_loss += loss
        
        total_loss.backward()
        optimizer.step()
        
        if iteration % 100 == 0:
            print(f"Iteration {iteration}: loss = {total_loss.item():.6f}")
    
    # Return optimized gaussians
    return {
        'xyz': means.detach().cpu(),
        'opacity': opacities.detach().cpu(),
        'dc': dc.detach().cpu(),
        'sh_rest': gaussians['sh_rest'],
        'scale': scales.detach().cpu(),
        'rotation': rotations.detach().cpu(),
    }


def save_gaussians(gaussians: dict, output_path: str, original_ply_path: str):
    """Save gaussians to PLY file, preserving original structure"""
    # Read original to get structure
    plydata = PlyData.read(original_ply_path)
    vertex = plydata['vertex']
    
    # Create new vertex data
    n_gaussians = len(gaussians['xyz'])
    
    # Get dtype from original - need to call it as property not method
    dtype = vertex.data.dtype
    
    # Create new array
    new_data = np.zeros(n_gaussians, dtype=dtype)
    
    # Fill in values
    xyz = gaussians['xyz'].numpy()
    new_data['x'] = xyz[:, 0]
    new_data['y'] = xyz[:, 1]
    new_data['z'] = xyz[:, 2]
    
    new_data['opacity'] = gaussians['opacity'].numpy()
    
    dc = gaussians['dc'].numpy()
    new_data['f_dc_0'] = dc[:, 0]
    new_data['f_dc_1'] = dc[:, 1]
    new_data['f_dc_2'] = dc[:, 2]
    
    scale = gaussians['scale'].numpy()
    new_data['scale_0'] = scale[:, 0]
    new_data['scale_1'] = scale[:, 1]
    new_data['scale_2'] = scale[:, 2]
    
    rot = gaussians['rotation'].numpy()
    new_data['rot_0'] = rot[:, 0]
    new_data['rot_1'] = rot[:, 1]
    new_data['rot_2'] = rot[:, 2]
    new_data['rot_3'] = rot[:, 3]
    
    # Copy SH rest if present
    if gaussians['sh_rest'] is not None:
        sh_rest = gaussians['sh_rest'].numpy()
        for i in range(sh_rest.shape[1]):
            key = f'f_rest_{i}'
            if key in dtype.names:
                new_data[key] = sh_rest[:, i]
    
    # Create new PLY
    from plyfile import PlyElement
    new_vertex = PlyElement.describe(new_data, 'vertex')
    PlyData([new_vertex]).write(output_path)
    print(f"Saved to {output_path}")


def main():
    parser = argparse.ArgumentParser(description='Gaussian Splatting Inpainting Pipeline')
    parser.add_argument('--input', type=str, required=True, help='Input PLY file')
    parser.add_argument('--output', type=str, required=True, help='Output PLY file')
    parser.add_argument('--mode', type=str, choices=['remove', 'complete'], default='remove',
                        help='remove: remove object from scene, complete: fill missing region')
    parser.add_argument('--object-indices', type=str, help='Path to numpy file with object gaussian indices')
    parser.add_argument('--n-views', type=int, default=32, help='Number of views for inpainting')
    parser.add_argument('--n-iters', type=int, default=500, help='Optimization iterations')
    args = parser.parse_args()
    
    print(f"Loading gaussians from {args.input}")
    gaussians = load_gaussians(args.input)
    print(f"Loaded {len(gaussians['xyz'])} gaussians")
    
    # Compute scene center and radius
    xyz = gaussians['xyz'].numpy()
    center = xyz.mean(axis=0)
    radius = np.linalg.norm(xyz - center, axis=1).max() * 1.5
    
    print(f"Scene center: {center}, radius: {radius}")
    
    # Generate cameras
    cameras = generate_orbit_cameras(
        center=center,
        radius=radius,
        elevation=np.radians(15),
        n_views=args.n_views,
        width=512,
        height=512,
        fov=60
    )
    print(f"Generated {len(cameras)} cameras")
    
    # Load object indices if provided
    if args.object_indices:
        object_indices = np.load(args.object_indices)
        print(f"Loaded {len(object_indices)} object indices")
    else:
        # Demo: use some indices
        print("No object indices provided, using demo")
        object_indices = np.arange(1000)  # First 1000 gaussians
    
    # Render and inpaint each view
    target_images = []
    masks = []
    
    for i, cam in enumerate(cameras):
        print(f"Processing view {i+1}/{len(cameras)}")
        
        # Render full scene
        rgb, depth = render_gaussians(gaussians, cam)
        rgb_np = (rgb.cpu().numpy() * 255).astype(np.uint8)
        
        # Create object mask
        mask = create_object_mask(gaussians, cam, object_indices)
        mask_np = (mask.cpu().numpy() * 255).astype(np.uint8)
        
        # Inpaint
        inpainted = inpaint_with_lama(rgb_np, mask_np)
        
        target_images.append(inpainted)
        masks.append(mask_np)
        
        # Save intermediate results
        if i < 4:
            Image.fromarray(rgb_np).save(f'/tmp/view_{i}_original.png')
            Image.fromarray(mask_np).save(f'/tmp/view_{i}_mask.png')
            Image.fromarray(inpainted).save(f'/tmp/view_{i}_inpainted.png')
    
    # Remove object gaussians
    if args.mode == 'remove':
        keep_mask = np.ones(len(gaussians['xyz']), dtype=bool)
        keep_mask[object_indices] = False
        
        filtered_gaussians = {
            k: v[keep_mask] if v is not None else None
            for k, v in gaussians.items()
        }
    else:
        filtered_gaussians = gaussians
    
    # Optimize to match inpainted views
    print("Optimizing gaussians...")
    optimized = optimize_gaussians(
        filtered_gaussians,
        cameras,
        target_images,
        masks,
        n_iters=args.n_iters
    )
    
    # Save result
    save_gaussians(optimized, args.output, args.input)
    print("Done!")


if __name__ == '__main__':
    main()
