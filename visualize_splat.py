#!/usr/bin/env python3
"""
Visualize Gaussian Splat Segmentation
Renders a comparison of original vs modified splat, highlighting moved objects.
"""

import numpy as np
from plyfile import PlyData
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
import json

def load_gaussian_positions(ply_path):
    """Load gaussian positions from PLY"""
    ply = PlyData.read(ply_path)
    positions = np.stack([
        ply['vertex']['x'],
        ply['vertex']['y'],
        ply['vertex']['z']
    ], axis=1)
    
    # Also try to get colors if available
    try:
        colors = np.stack([
            ply['vertex']['f_dc_0'],
            ply['vertex']['f_dc_1'],
            ply['vertex']['f_dc_2']
        ], axis=1)
        # DC coefficients are in SH space, rough conversion
        colors = np.clip(colors * 0.28 + 0.5, 0, 1)
    except:
        colors = None
        
    return positions, colors


def visualize_comparison(original_path, modified_path, labels_path, output_path):
    """Create comparison visualization"""
    
    # Load both versions
    print("Loading original splat...")
    orig_pos, orig_colors = load_gaussian_positions(original_path)
    
    print("Loading modified splat...")
    mod_pos, mod_colors = load_gaussian_positions(modified_path)
    
    # Find which points moved (significant displacement)
    displacement = np.linalg.norm(mod_pos - orig_pos, axis=1)
    moved_mask = displacement > 0.01  # 1cm threshold
    
    print(f"Total gaussians: {len(orig_pos)}")
    print(f"Moved gaussians: {moved_mask.sum()}")
    
    # Subsample for visualization (too many points otherwise)
    n_sample = min(50000, len(orig_pos))
    indices = np.random.choice(len(orig_pos), n_sample, replace=False)
    
    # Keep all moved points
    moved_indices = np.where(moved_mask)[0]
    static_indices = np.setdiff1d(indices, moved_indices)
    static_indices = static_indices[:n_sample - len(moved_indices)]
    
    vis_indices = np.concatenate([static_indices, moved_indices])
    
    fig = plt.figure(figsize=(16, 8))
    
    # Original
    ax1 = fig.add_subplot(121, projection='3d')
    ax1.scatter(
        orig_pos[vis_indices, 0],
        orig_pos[vis_indices, 1],
        orig_pos[vis_indices, 2],
        c=orig_colors[vis_indices] if orig_colors is not None else 'blue',
        s=0.5,
        alpha=0.5
    )
    
    # Highlight moved region in original
    if moved_indices.size > 0:
        ax1.scatter(
            orig_pos[moved_indices, 0],
            orig_pos[moved_indices, 1],
            orig_pos[moved_indices, 2],
            c='red',
            s=2,
            alpha=0.8,
            label='Object to move'
        )
    ax1.set_title('Original Splat')
    ax1.set_xlabel('X')
    ax1.set_ylabel('Y')
    ax1.set_zlabel('Z')
    ax1.legend()
    
    # Modified
    ax2 = fig.add_subplot(122, projection='3d')
    ax2.scatter(
        mod_pos[vis_indices, 0],
        mod_pos[vis_indices, 1],
        mod_pos[vis_indices, 2],
        c=mod_colors[vis_indices] if mod_colors is not None else 'blue',
        s=0.5,
        alpha=0.5
    )
    
    # Highlight moved region in modified
    if moved_indices.size > 0:
        ax2.scatter(
            mod_pos[moved_indices, 0],
            mod_pos[moved_indices, 1],
            mod_pos[moved_indices, 2],
            c='green',
            s=2,
            alpha=0.8,
            label='Moved object'
        )
    ax2.set_title('Modified Splat (Object Moved)')
    ax2.set_xlabel('X')
    ax2.set_ylabel('Y')
    ax2.set_zlabel('Z')
    ax2.legend()
    
    # Match view angles
    for ax in [ax1, ax2]:
        ax.view_init(elev=20, azim=45)
        ax.set_xlim(orig_pos[:, 0].min(), orig_pos[:, 0].max())
        ax.set_ylim(orig_pos[:, 1].min(), orig_pos[:, 1].max())
        ax.set_zlim(orig_pos[:, 2].min(), orig_pos[:, 2].max())
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    print(f"Saved visualization to {output_path}")
    plt.close()
    
    return moved_mask.sum()


def visualize_object_extraction(splat_path, labels_path, output_path):
    """Visualize segmented objects with different colors"""
    from gaussian_segment import GaussianSplat, BoundingBox, SceneObject
    
    print("Loading splat and segmenting...")
    splat = GaussianSplat(splat_path)
    objects = splat.segment_objects(labels_path, margin=0.05)
    
    # Assign colors by object
    colors = np.ones((len(splat.positions), 3)) * 0.5  # Gray base
    
    # Color palette for objects
    palette = plt.cm.tab20(np.linspace(0, 1, 20))[:, :3]
    
    for i, obj in enumerate(objects):
        color = palette[i % len(palette)]
        colors[obj.gaussian_indices] = color
    
    # Subsample
    n_sample = min(80000, len(splat.positions))
    indices = np.random.choice(len(splat.positions), n_sample, replace=False)
    
    fig = plt.figure(figsize=(12, 10))
    ax = fig.add_subplot(111, projection='3d')
    
    ax.scatter(
        splat.positions[indices, 0],
        splat.positions[indices, 1],
        splat.positions[indices, 2],
        c=colors[indices],
        s=0.3,
        alpha=0.6
    )
    
    ax.set_title(f'Segmented Objects ({len(objects)} detected)')
    ax.set_xlabel('X')
    ax.set_ylabel('Y')
    ax.set_zlabel('Z')
    ax.view_init(elev=25, azim=135)
    
    # Add legend
    handles = []
    for i, obj in enumerate(objects[:10]):  # First 10
        color = palette[i % len(palette)]
        from matplotlib.patches import Patch
        handles.append(Patch(color=color, label=f'{obj.label} ({len(obj.gaussian_indices)} pts)'))
    ax.legend(handles=handles, loc='upper left', fontsize=8)
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    print(f"Saved segmentation visualization to {output_path}")
    plt.close()


if __name__ == "__main__":
    original = "/home/ubuntu/datasets/sage_batch_walking/0001_839920_seq_006_fruit/gs_output_15k/point_cloud/iteration_15000/point_cloud.ply"
    modified = "/home/ubuntu/.openclaw/workspace/modified_splat.ply"
    labels = "/home/ubuntu/datasets/scenes/0001_839920/labels.json"
    
    # Comparison view
    visualize_comparison(
        original, 
        modified, 
        labels,
        "/home/ubuntu/.openclaw/workspace/splat_comparison.png"
    )
    
    # Segmentation view
    visualize_object_extraction(
        original,
        labels,
        "/home/ubuntu/.openclaw/workspace/splat_segmentation.png"
    )
