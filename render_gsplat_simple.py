#!/usr/bin/env python3
"""
Render gaussian splat using gsplat library
"""

import torch
import numpy as np
from plyfile import PlyData
import json
from PIL import Image
from pathlib import Path

# Set up device
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Using device: {device}")

def load_splat(ply_path):
    """Load gaussian splat PLY"""
    print(f"Loading {ply_path}...")
    ply = PlyData.read(ply_path)
    v = ply['vertex'].data
    
    xyz = torch.tensor(np.stack([v['x'], v['y'], v['z']], axis=1), dtype=torch.float32, device=device)
    
    # SH coefficients
    SH_C0 = 0.28209479177387814
    sh_dc = np.stack([v['f_dc_0'], v['f_dc_1'], v['f_dc_2']], axis=1)
    
    # Higher order SH
    n_sh_rest = 45  # 3 * 15 = 45 coefficients for degree 3
    sh_rest = []
    for i in range(n_sh_rest):
        sh_rest.append(v[f'f_rest_{i}'])
    sh_rest = np.stack(sh_rest, axis=1)  # [N, 45]
    
    # Reshape to [N, 16, 3] - 16 SH bands, 3 color channels
    # DC is band 0, rest are bands 1-15
    sh_all = np.zeros((len(xyz), 16, 3), dtype=np.float32)
    sh_all[:, 0, :] = sh_dc  # DC term
    sh_rest_reshaped = sh_rest.reshape(-1, 15, 3)  # [N, 15, 3]
    sh_all[:, 1:, :] = sh_rest_reshaped
    
    colors = torch.tensor(sh_all, dtype=torch.float32, device=device)
    
    # Opacity
    opacity_logit = torch.tensor(v['opacity'], dtype=torch.float32, device=device)
    opacity = torch.sigmoid(opacity_logit)
    
    # Scales
    scales = torch.exp(torch.tensor(
        np.stack([v['scale_0'], v['scale_1'], v['scale_2']], axis=1),
        dtype=torch.float32, device=device
    ))
    
    # Quaternions (wxyz)
    quats = torch.tensor(
        np.stack([v['rot_0'], v['rot_1'], v['rot_2'], v['rot_3']], axis=1),
        dtype=torch.float32, device=device
    )
    quats = quats / quats.norm(dim=1, keepdim=True)
    
    print(f"  Loaded {len(xyz):,} gaussians")
    return xyz, colors, opacity, scales, quats


def main():
    ply_path = "/home/ubuntu/datasets/sage_batch_walking/0001_839920_seq_006_fruit/gs_output_15k/point_cloud/iteration_15000/point_cloud.ply"
    cam_path = "/home/ubuntu/datasets/sage_batch_walking/0001_839920_seq_006_fruit/gs_output_15k/cameras.json"
    output_dir = Path("/home/ubuntu/.openclaw/workspace")
    
    # Load splat
    xyz, colors, opacity, scales, quats = load_splat(ply_path)
    
    # Load camera
    with open(cam_path) as f:
        cams = json.load(f)
    
    cam = cams[15]  # Middle frame
    print(f"\nUsing camera: {cam['img_name']}")
    
    W, H = cam['width'], cam['height']
    fx, fy = cam['fx'], cam['fy']
    cx, cy = W / 2, H / 2
    
    # Camera pose
    R = torch.tensor(cam['rotation'], dtype=torch.float32, device=device)
    t = torch.tensor(cam['position'], dtype=torch.float32, device=device)
    
    # Camera matrix (world to camera)
    viewmat = torch.eye(4, device=device)
    viewmat[:3, :3] = R.T
    viewmat[:3, 3] = -R.T @ t
    
    print(f"  Resolution: {W}x{H}")
    print(f"  Focal: fx={fx:.1f}, fy={fy:.1f}")
    
    # Try gsplat rasterization
    print("\nRendering with gsplat...")
    
    try:
        from gsplat import rasterization
        
        # For single camera, remove the first batch dim since gsplat treats 
        # the last dim before C as batch. We have just 1 camera (C=1).
        means = xyz  # [N, 3]
        quats_gsplat = quats  # [N, 4]
        scales_gsplat = scales  # [N, 3]
        opacities = opacity  # [N]
        colors_gsplat = colors  # [N, K, 3]
        
        viewmats = viewmat.unsqueeze(0)  # [C=1, 4, 4]
        
        # Intrinsics
        Ks = torch.tensor([[[fx, 0, cx], [0, fy, cy], [0, 0, 1]]], 
                         dtype=torch.float32, device=device)  # [C=1, 3, 3]
        
        print(f"  means: {means.shape}")
        print(f"  quats: {quats_gsplat.shape}")
        print(f"  scales: {scales_gsplat.shape}")
        print(f"  opacities: {opacities.shape}")
        print(f"  colors: {colors_gsplat.shape}")
        print(f"  viewmats: {viewmats.shape}")
        print(f"  Ks: {Ks.shape}")
        
        renders, alphas, info = rasterization(
            means=means,
            quats=quats_gsplat,
            scales=scales_gsplat,
            opacities=opacities,
            colors=colors_gsplat,
            viewmats=viewmats,
            Ks=Ks,
            width=W,
            height=H,
            sh_degree=3,
            near_plane=0.01,
            far_plane=100.0,
        )
        
        print(f"  Output shape: {renders.shape}")
        
        # Convert to image
        img = renders[0].clamp(0, 1).cpu().numpy()
        img = (img * 255).astype(np.uint8)
        
        Image.fromarray(img).save(output_dir / "gsplat_render.png")
        print(f"\nSaved: gsplat_render.png")
        
    except Exception as e:
        print(f"gsplat failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
