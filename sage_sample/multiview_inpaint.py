#!/usr/bin/env python3
"""
Multi-view Gaussian Splatting Inpainting

This script:
1. Removes an object (chair) from a gaussian splat
2. Renders multiple views of the scene with the hole
3. Inpaints each view using Stable Diffusion
4. Optimizes new gaussians to fill the hole based on inpainted views

Usage:
    CUDA_VISIBLE_DEVICES=1 python multiview_inpaint.py
"""

import os
import sys
import json
import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from pathlib import Path
from scipy import ndimage
import argparse

# Set GPU before importing heavy libs
os.environ.setdefault('CUDA_VISIBLE_DEVICES', '1')

from inpaint_pipeline import (
    load_gaussians, render_gaussians, Camera, 
    generate_orbit_cameras, save_gaussians
)

def make_lookat_camera(eye, target, up_hint=np.array([0, 0, 1]), width=512, height=512, fov=60):
    """Create a camera looking at target from eye position"""
    forward = target - eye
    forward = forward / np.linalg.norm(forward)
    right = np.cross(forward, up_hint)
    rn = np.linalg.norm(right)
    if rn < 0.001:
        up_hint = np.array([0, 1, 0])
        right = np.cross(forward, up_hint)
        rn = np.linalg.norm(right)
    right = right / rn
    up = np.cross(right, forward)
    c2w = np.eye(4, dtype=np.float32)
    c2w[:3, 0] = right
    c2w[:3, 1] = up
    c2w[:3, 2] = -forward
    c2w[:3, 3] = eye
    fx = fy = width / (2 * np.tan(np.radians(fov / 2)))
    return Camera(fx, fy, width/2, height/2, width, height, c2w)


def get_object_indices(xyz, labels, object_id, margin=0.05):
    """Get gaussian indices that belong to an object based on its bbox"""
    obj = next((o for o in labels if o['ins_id'] == object_id), None)
    if obj is None:
        raise ValueError(f"Object {object_id} not found")
    
    bbox_pts = np.array([[p['x'], p['y'], p['z']] for p in obj['bounding_box']])
    bbox_min = bbox_pts.min(axis=0)
    bbox_max = bbox_pts.max(axis=0)
    
    in_bbox = (
        (xyz[:, 0] >= bbox_min[0] - margin) & (xyz[:, 0] <= bbox_max[0] + margin) &
        (xyz[:, 1] >= bbox_min[1] - margin) & (xyz[:, 1] <= bbox_max[1] + margin) &
        (xyz[:, 2] >= bbox_min[2] - margin) & (xyz[:, 2] <= bbox_max[2] + margin)
    )
    return np.where(in_bbox)[0], bbox_pts.mean(axis=0)


def subset_gaussians(gaussians, indices):
    """Extract a subset of gaussians by index"""
    xyz = gaussians['xyz']
    n = len(xyz) if isinstance(xyz, np.ndarray) else xyz.shape[0]
    
    result = {}
    for k, v in gaussians.items():
        if isinstance(v, np.ndarray) and len(v) == n:
            result[k] = v[indices]
        elif isinstance(v, torch.Tensor) and v.shape[0] == n:
            result[k] = v[indices]
        else:
            result[k] = v
    return result


def render_with_flip(gaussians, camera, bg_color=None):
    """Render and flip vertically to correct orientation"""
    if bg_color is None:
        bg_color = torch.tensor([1., 1., 1.], device='cuda')
    with torch.no_grad():
        rgb, depth = render_gaussians(gaussians, camera, bg_color=bg_color)
    rgb_np = (rgb.cpu().numpy() * 255).clip(0, 255).astype(np.uint8)
    return np.flipud(rgb_np)


def inpaint_sd(image, mask, prompt="indoor floor tiles, clean interior", device='cuda'):
    """Inpaint using Stable Diffusion"""
    from diffusers import StableDiffusionInpaintPipeline
    
    # Load pipeline (cache it if called multiple times)
    if not hasattr(inpaint_sd, '_pipe'):
        print("Loading SD inpainting model...")
        inpaint_sd._pipe = StableDiffusionInpaintPipeline.from_pretrained(
            'runwayml/stable-diffusion-inpainting',
            torch_dtype=torch.float16,
            safety_checker=None
        ).to(device)
        inpaint_sd._pipe.set_progress_bar_config(disable=True)
    
    pipe = inpaint_sd._pipe
    
    # Dilate mask
    mask_dilated = ndimage.binary_dilation(mask > 128, iterations=5).astype(np.uint8) * 255
    
    # Convert to PIL
    image_pil = Image.fromarray(image).convert('RGB')
    mask_pil = Image.fromarray(mask_dilated).convert('L')
    
    # Inpaint
    result = pipe(
        prompt=prompt,
        image=image_pil,
        mask_image=mask_pil,
        num_inference_steps=30,
        guidance_scale=7.5,
    ).images[0]
    
    return np.array(result)


def optimize_hole_gaussians(
    scene_gaussians,
    hole_mask_indices,  # Indices of removed gaussians
    cameras,
    target_images,  # Inpainted images
    masks,  # Where to supervise
    n_iters=500,
    lr=0.005,
    device='cuda'
):
    """
    Optimize new gaussians to fill the hole.
    
    Instead of optimizing ALL scene gaussians, we:
    1. Keep existing scene gaussians frozen
    2. Initialize new gaussians in the hole region
    3. Optimize only the new gaussians
    """
    print(f"Optimizing {len(hole_mask_indices)} gaussians to fill hole...")
    
    # Get the positions where removed gaussians were
    hole_xyz = scene_gaussians['xyz'][hole_mask_indices].clone().to(device)
    n_new = len(hole_xyz)
    
    # Initialize new gaussians at hole positions with some noise
    new_xyz = hole_xyz + torch.randn_like(hole_xyz) * 0.01
    new_xyz.requires_grad_(True)
    
    # Initialize other params from removed gaussians (as starting point)
    new_opacity = scene_gaussians['opacity'][hole_mask_indices].clone().to(device)
    new_opacity.requires_grad_(True)
    
    new_dc = scene_gaussians['dc'][hole_mask_indices].clone().to(device)
    new_dc.requires_grad_(True)
    
    new_scale = scene_gaussians['scale'][hole_mask_indices].clone().to(device)
    new_scale.requires_grad_(True)
    
    new_rot = scene_gaussians['rotation'][hole_mask_indices].clone().to(device)
    new_rot.requires_grad_(True)
    
    # Keep scene (without hole) frozen
    other_indices = np.setdiff1d(np.arange(len(scene_gaussians['xyz'])), hole_mask_indices)
    frozen_gaussians = subset_gaussians(scene_gaussians, other_indices)
    for k, v in frozen_gaussians.items():
        if isinstance(v, torch.Tensor):
            frozen_gaussians[k] = v.to(device)
    
    # Optimizer
    optimizer = torch.optim.Adam([
        {'params': new_xyz, 'lr': lr},
        {'params': new_opacity, 'lr': lr * 0.1},
        {'params': new_dc, 'lr': lr},
        {'params': new_scale, 'lr': lr * 0.1},
        {'params': new_rot, 'lr': lr * 0.1},
    ])
    
    # Convert targets
    targets = [torch.tensor(img / 255.0, dtype=torch.float32, device=device) 
               for img in target_images]
    mask_tensors = [torch.tensor(m / 255.0, dtype=torch.float32, device=device) 
                    for m in masks]
    
    bg_color = torch.tensor([1., 1., 1.], device=device)
    
    for iteration in range(n_iters):
        optimizer.zero_grad()
        total_loss = 0
        
        # Combine frozen + new gaussians
        combined = {
            'xyz': torch.cat([frozen_gaussians['xyz'], new_xyz]),
            'opacity': torch.cat([frozen_gaussians['opacity'], new_opacity]),
            'dc': torch.cat([frozen_gaussians['dc'], new_dc]),
            'scale': torch.cat([frozen_gaussians['scale'], new_scale]),
            'rotation': torch.cat([frozen_gaussians['rotation'], new_rot]),
            'sh_rest': None,
        }
        
        for cam, target, mask in zip(cameras, targets, mask_tensors):
            rgb, _ = render_gaussians(combined, cam, device, bg_color=bg_color)
            
            # Flip to match target orientation
            rgb = torch.flip(rgb, [0])
            
            # L1 loss in masked region
            loss = (torch.abs(rgb - target) * mask.unsqueeze(-1)).mean()
            total_loss += loss
        
        total_loss.backward()
        optimizer.step()
        
        if iteration % 50 == 0:
            print(f"  Iter {iteration}: loss = {total_loss.item():.6f}")
    
    # Return optimized new gaussians combined with frozen scene
    result = {
        'xyz': torch.cat([frozen_gaussians['xyz'].cpu(), new_xyz.detach().cpu()]),
        'opacity': torch.cat([frozen_gaussians['opacity'].cpu(), new_opacity.detach().cpu()]),
        'dc': torch.cat([frozen_gaussians['dc'].cpu(), new_dc.detach().cpu()]),
        'scale': torch.cat([frozen_gaussians['scale'].cpu(), new_scale.detach().cpu()]),
        'rotation': torch.cat([frozen_gaussians['rotation'].cpu(), new_rot.detach().cpu()]),
        'sh_rest': scene_gaussians.get('sh_rest'),
    }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scene', default='0001_839920', help='Scene directory')
    parser.add_argument('--object-id', default='67', help='Object ID to remove')
    parser.add_argument('--n-views', type=int, default=6, help='Number of views for inpainting')
    parser.add_argument('--n-iters', type=int, default=300, help='Optimization iterations')
    parser.add_argument('--output', default='scene_inpainted.ply', help='Output PLY file')
    parser.add_argument('--skip-optim', action='store_true', help='Skip optimization, just do 2D inpainting')
    args = parser.parse_args()
    
    scene_dir = Path(args.scene)
    ply_path = scene_dir / '3dgs_decompressed.ply'
    labels_path = scene_dir / 'labels.json'
    
    print(f"Loading scene from {ply_path}...")
    gaussians = load_gaussians(str(ply_path))
    xyz = gaussians['xyz'].numpy() if isinstance(gaussians['xyz'], torch.Tensor) else gaussians['xyz']
    
    print(f"Loading labels from {labels_path}...")
    with open(labels_path) as f:
        labels = json.load(f)
    
    # Find object
    obj_indices, obj_center = get_object_indices(xyz, labels, args.object_id)
    print(f"Found {len(obj_indices)} gaussians for object {args.object_id}")
    print(f"Object center: {obj_center}")
    
    # Generate cameras around the object
    print(f"Generating {args.n_views} camera views...")
    cameras = []
    angles = np.linspace(0, 2*np.pi, args.n_views, endpoint=False)
    for i, angle in enumerate(angles):
        # Position cameras in a circle around object
        radius = 3.0
        height_offset = 0.5
        pos = obj_center + np.array([
            radius * np.cos(angle),
            radius * np.sin(angle),
            height_offset
        ], dtype=np.float32)
        cam = make_lookat_camera(pos, obj_center.astype(np.float32))
        cameras.append(cam)
    
    # Create output directory
    out_dir = Path('inpaint_output')
    out_dir.mkdir(exist_ok=True)
    
    # Render scene with object, create masks, render without object
    print("Rendering views...")
    scene_with_obj = []
    scene_no_obj = []
    obj_masks = []
    
    other_indices = np.setdiff1d(np.arange(len(xyz)), obj_indices)
    gaussians_no_obj = subset_gaussians(gaussians, other_indices)
    gaussians_obj_only = subset_gaussians(gaussians, obj_indices)
    
    for i, cam in enumerate(cameras):
        # Full scene
        img_full = render_with_flip(gaussians, cam)
        scene_with_obj.append(img_full)
        Image.fromarray(img_full).save(out_dir / f'view_{i}_full.png')
        
        # Scene without object
        img_no_obj = render_with_flip(gaussians_no_obj, cam)
        scene_no_obj.append(img_no_obj)
        Image.fromarray(img_no_obj).save(out_dir / f'view_{i}_no_obj.png')
        
        # Object mask
        img_obj = render_with_flip(gaussians_obj_only, cam, 
                                   bg_color=torch.tensor([0., 0., 0.], device='cuda'))
        mask = (img_obj.sum(axis=-1) > 10).astype(np.uint8) * 255
        obj_masks.append(mask)
        Image.fromarray(mask).save(out_dir / f'view_{i}_mask.png')
    
    # Inpaint each view
    print("Inpainting views with Stable Diffusion...")
    inpainted = []
    for i, (img, mask) in enumerate(zip(scene_no_obj, obj_masks)):
        print(f"  Inpainting view {i}...")
        result = inpaint_sd(img, mask)
        inpainted.append(result)
        Image.fromarray(result).save(out_dir / f'view_{i}_inpainted.png')
    
    if args.skip_optim:
        print("Skipping optimization (--skip-optim)")
        print(f"2D inpainted views saved to {out_dir}/")
        return
    
    # Optimize gaussians to match inpainted views
    print("Optimizing gaussians to match inpainted views...")
    optimized = optimize_hole_gaussians(
        gaussians,
        obj_indices,
        cameras,
        inpainted,
        obj_masks,
        n_iters=args.n_iters,
    )
    
    # Save result
    output_path = out_dir / args.output
    print(f"Saving optimized gaussians to {output_path}...")
    save_gaussians(optimized, str(output_path), str(ply_path))
    
    # Render final result for comparison
    print("Rendering final comparison...")
    for i, cam in enumerate(cameras):
        final = render_with_flip(optimized, cam)
        Image.fromarray(final).save(out_dir / f'view_{i}_final.png')
    
    print("Done!")
    print(f"Results saved to {out_dir}/")


if __name__ == '__main__':
    main()
