#!/usr/bin/env python3
"""
Segment objects from SAGE/InteriorGS gaussian splats using bounding boxes.

Usage:
    python segment_sage.py <scene_dir> --label "chair" --output chair.ply
    python segment_sage.py <scene_dir> --label "table" --all --output-dir objects/
    python segment_sage.py <scene_dir> --list  # List all available labels
"""

import argparse
import json
import numpy as np
from pathlib import Path
from plyfile import PlyData, PlyElement

def load_ply(ply_path):
    """Load a PLY file and return vertex data."""
    ply = PlyData.read(ply_path)
    return ply['vertex'].data

def points_in_bbox(points, bbox_corners):
    """Check if points are inside the 8-corner bounding box (axis-aligned approximation)."""
    corners = np.array([[c['x'], c['y'], c['z']] for c in bbox_corners])
    min_pt = corners.min(axis=0)
    max_pt = corners.max(axis=0)
    
    inside = ((points[:, 0] >= min_pt[0]) & (points[:, 0] <= max_pt[0]) &
              (points[:, 1] >= min_pt[1]) & (points[:, 1] <= max_pt[1]) &
              (points[:, 2] >= min_pt[2]) & (points[:, 2] <= max_pt[2]))
    return inside

def save_ply(vertex_data, output_path, minimal=False):
    """Save vertex data to PLY file."""
    if minimal:
        # Create minimal 14-property format for web viewers
        dtype = [
            ('x', 'f4'), ('y', 'f4'), ('z', 'f4'),
            ('f_dc_0', 'f4'), ('f_dc_1', 'f4'), ('f_dc_2', 'f4'),
            ('opacity', 'f4'),
            ('scale_0', 'f4'), ('scale_1', 'f4'), ('scale_2', 'f4'),
            ('rot_0', 'f4'), ('rot_1', 'f4'), ('rot_2', 'f4'), ('rot_3', 'f4'),
        ]
        out = np.empty(len(vertex_data), dtype=dtype)
        for name in ['x', 'y', 'z', 'f_dc_0', 'f_dc_1', 'f_dc_2', 'opacity',
                     'scale_0', 'scale_1', 'scale_2', 'rot_0', 'rot_1', 'rot_2', 'rot_3']:
            out[name] = vertex_data[name]
        vertex_data = out
    
    el = PlyElement.describe(vertex_data, 'vertex')
    PlyData([el], text=False).write(str(output_path))

def normalize_for_viewer(vertex_data):
    """Normalize positions and clamp f_dc values for web viewer compatibility."""
    # Center positions
    x, y, z = vertex_data['x'].copy(), vertex_data['y'].copy(), vertex_data['z'].copy()
    center = np.array([x.mean(), y.mean(), z.mean()])
    x -= center[0]
    y -= center[1]
    z -= center[2]
    
    # Scale to ~2 unit span
    max_extent = max(x.max() - x.min(), y.max() - y.min(), z.max() - z.min())
    if max_extent > 0:
        scale_factor = 2.0 / max_extent
        x *= scale_factor
        y *= scale_factor
        z *= scale_factor
        scale_adj = np.log(scale_factor)
    else:
        scale_adj = 0
    
    # Create new array with normalized values
    out = np.array(vertex_data)
    out['x'] = x
    out['y'] = y
    out['z'] = z
    out['scale_0'] = vertex_data['scale_0'] + scale_adj
    out['scale_1'] = vertex_data['scale_1'] + scale_adj
    out['scale_2'] = vertex_data['scale_2'] + scale_adj
    
    return out

def main():
    parser = argparse.ArgumentParser(description='Segment objects from SAGE gaussian splats')
    parser.add_argument('scene_dir', type=Path, help='Path to scene directory (contains 3dgs_decompressed.ply and labels.json)')
    parser.add_argument('--label', type=str, help='Object label to extract (e.g., "chair", "table")')
    parser.add_argument('--instance', type=str, help='Specific instance ID to extract')
    parser.add_argument('--all', action='store_true', help='Extract all instances of the label')
    parser.add_argument('--output', '-o', type=Path, help='Output PLY file path')
    parser.add_argument('--output-dir', type=Path, help='Output directory for --all mode')
    parser.add_argument('--list', action='store_true', help='List all available labels')
    parser.add_argument('--minimal', action='store_true', help='Output minimal 14-property format for web viewers')
    parser.add_argument('--normalize', action='store_true', help='Normalize positions for web viewer compatibility')
    
    args = parser.parse_args()
    
    # Find PLY file (compressed or decompressed)
    ply_path = args.scene_dir / '3dgs_decompressed.ply'
    if not ply_path.exists():
        ply_path = args.scene_dir / '3dgs_compressed.ply'
        if ply_path.exists():
            print(f"Found compressed PLY. Run: splat-transform {ply_path} {args.scene_dir}/3dgs_decompressed.ply")
            return 1
        print(f"No PLY file found in {args.scene_dir}")
        return 1
    
    labels_path = args.scene_dir / 'labels.json'
    if not labels_path.exists():
        print(f"No labels.json found in {args.scene_dir}")
        return 1
    
    # Load data
    print(f"Loading {ply_path}...")
    vertex = load_ply(ply_path)
    positions = np.stack([vertex['x'], vertex['y'], vertex['z']], axis=1)
    print(f"Loaded {len(vertex):,} gaussians")
    
    with open(labels_path) as f:
        labels = json.load(f)
    print(f"Loaded {len(labels)} object annotations")
    
    # List mode
    if args.list:
        from collections import Counter
        label_counts = Counter(obj['label'] for obj in labels)
        print(f"\nAvailable labels ({len(label_counts)} types):")
        for label, count in sorted(label_counts.items(), key=lambda x: -x[1]):
            print(f"  {label}: {count}")
        return 0
    
    if not args.label and not args.instance:
        print("Specify --label or --instance to extract, or --list to see available labels")
        return 1
    
    # Find matching objects
    if args.instance:
        objects = [obj for obj in labels if obj['ins_id'] == args.instance]
    else:
        objects = [obj for obj in labels if obj['label'] == args.label]
    
    if not objects:
        print(f"No objects found matching criteria")
        return 1
    
    print(f"Found {len(objects)} matching objects")
    
    # Extract single or all
    if args.all:
        output_dir = args.output_dir or Path('.')
        output_dir.mkdir(parents=True, exist_ok=True)
        
        for obj in objects:
            mask = points_in_bbox(positions, obj['bounding_box'])
            if mask.sum() == 0:
                print(f"  {obj['label']}_{obj['ins_id']}: no gaussians found, skipping")
                continue
            
            obj_data = vertex[mask]
            if args.normalize:
                obj_data = normalize_for_viewer(obj_data)
            
            output_path = output_dir / f"{obj['label']}_{obj['ins_id']}.ply"
            save_ply(obj_data, output_path, minimal=args.minimal)
            print(f"  {output_path}: {len(obj_data):,} gaussians")
    else:
        # Combine all matching objects
        combined_mask = np.zeros(len(vertex), dtype=bool)
        for obj in objects:
            mask = points_in_bbox(positions, obj['bounding_box'])
            combined_mask |= mask
        
        obj_data = vertex[combined_mask]
        if len(obj_data) == 0:
            print("No gaussians found in bounding boxes")
            return 1
        
        if args.normalize:
            obj_data = normalize_for_viewer(obj_data)
        
        output_path = args.output or Path(f"{args.label or args.instance}.ply")
        save_ply(obj_data, output_path, minimal=args.minimal)
        print(f"Saved {len(obj_data):,} gaussians to {output_path}")
    
    return 0

if __name__ == '__main__':
    exit(main())
