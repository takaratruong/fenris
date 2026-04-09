#!/usr/bin/env python3
"""
Mirror-based gaussian splat completion.
Reflects gaussians from above a contact plane to fill occluded bottom regions.
"""

import numpy as np
from plyfile import PlyData, PlyElement
import argparse

def load_ply(path):
    """Load gaussian splat PLY file."""
    ply = PlyData.read(path)
    vertex = ply['vertex']
    return vertex, ply

def analyze_z_distribution(vertex):
    """Analyze Z distribution to find floor contact plane."""
    z = vertex['z']
    print(f"Z range: {z.min():.4f} to {z.max():.4f}")
    print(f"Z mean: {z.mean():.4f}, std: {z.std():.4f}")
    
    # Histogram to find floor level
    hist, edges = np.histogram(z, bins=50)
    floor_bin = np.argmax(hist[:10])  # Floor is likely in bottom 20%
    floor_z = edges[floor_bin]
    print(f"Detected floor Z (peak in lower region): {floor_z:.4f}")
    return floor_z

def mirror_gaussians(vertex, floor_z, mirror_band=0.05, max_mirror_depth=0.1):
    """
    Mirror gaussians from just above floor to create bottom fill.
    
    Args:
        vertex: PLY vertex data
        floor_z: Z coordinate of floor/contact plane
        mirror_band: How far above floor to sample gaussians for mirroring (meters)
        max_mirror_depth: Maximum depth below floor to mirror to (meters)
    """
    z = vertex['z']
    
    # Select gaussians in the mirror band (just above floor)
    band_mask = (z >= floor_z) & (z <= floor_z + mirror_band)
    source_indices = np.where(band_mask)[0]
    print(f"Gaussians in mirror band [{floor_z:.3f}, {floor_z + mirror_band:.3f}]: {len(source_indices)}")
    
    if len(source_indices) == 0:
        print("No gaussians in mirror band - adjusting...")
        # Fall back to bottom 10% of gaussians
        z_threshold = np.percentile(z, 10)
        source_indices = np.where(z <= z_threshold)[0]
        print(f"Using bottom 10% ({len(source_indices)} gaussians) instead")
    
    # Create mirrored gaussians
    mirrored_data = {}
    for name in vertex.data.dtype.names:
        original = vertex[name]
        mirrored_data[name] = original[source_indices].copy()
    
    # Mirror Z coordinates around floor plane
    original_z = mirrored_data['z']
    distance_above_floor = original_z - floor_z
    mirrored_data['z'] = floor_z - distance_above_floor
    
    # Clip to max mirror depth
    min_z = floor_z - max_mirror_depth
    valid_mask = mirrored_data['z'] >= min_z
    for name in mirrored_data:
        mirrored_data[name] = mirrored_data[name][valid_mask]
    
    print(f"Created {len(mirrored_data['z'])} mirrored gaussians")
    print(f"Mirrored Z range: {mirrored_data['z'].min():.4f} to {mirrored_data['z'].max():.4f}")
    
    # Flip rotation quaternion's Z component for proper orientation
    # Quaternion (w, x, y, z) -> mirror around XY plane flips z component
    if 'rot_2' in mirrored_data:  # rot_2 is the z component in wxyz format (rot_0=w, rot_1=x, rot_2=y, rot_3=z)
        # Actually for mirroring around XY plane (z=const), we flip the quat's x and y
        # components to maintain proper rotation direction
        mirrored_data['rot_1'] = -mirrored_data['rot_1']  # Flip x
        mirrored_data['rot_2'] = -mirrored_data['rot_2']  # Flip y
    
    # Reduce opacity slightly for mirrored gaussians (they're inferred)
    if 'opacity' in mirrored_data:
        mirrored_data['opacity'] = mirrored_data['opacity'] * 0.8
    
    return mirrored_data, len(mirrored_data['z'])

def combine_and_save(vertex, mirrored_data, output_path):
    """Combine original and mirrored gaussians, save to PLY."""
    # Get dtype from original
    dtype = vertex.data.dtype
    
    # Combine arrays
    n_original = len(vertex.data)
    n_mirrored = len(mirrored_data['z'])
    n_total = n_original + n_mirrored
    
    combined = np.zeros(n_total, dtype=dtype)
    
    # Copy original data
    for name in dtype.names:
        combined[name][:n_original] = vertex[name]
        combined[name][n_original:] = mirrored_data[name]
    
    # Create PLY
    el = PlyElement.describe(combined, 'vertex')
    PlyData([el], text=False).write(output_path)
    print(f"Saved {n_total} gaussians ({n_original} original + {n_mirrored} mirrored) to {output_path}")

def main():
    parser = argparse.ArgumentParser(description='Mirror-based gaussian completion')
    parser.add_argument('input', help='Input PLY file')
    parser.add_argument('output', help='Output PLY file')
    parser.add_argument('--floor-z', type=float, help='Floor Z coordinate (auto-detect if not specified)')
    parser.add_argument('--band', type=float, default=0.03, help='Mirror band width above floor (meters)')
    parser.add_argument('--depth', type=float, default=0.05, help='Max mirror depth below floor (meters)')
    args = parser.parse_args()
    
    print(f"Loading {args.input}...")
    vertex, ply = load_ply(args.input)
    print(f"Loaded {len(vertex.data)} gaussians")
    
    # Detect or use provided floor Z
    if args.floor_z is not None:
        floor_z = args.floor_z
        print(f"Using provided floor Z: {floor_z}")
    else:
        floor_z = analyze_z_distribution(vertex)
    
    # Mirror gaussians
    mirrored_data, n_mirrored = mirror_gaussians(vertex, floor_z, args.band, args.depth)
    
    # Save combined result
    combine_and_save(vertex, mirrored_data, args.output)

if __name__ == '__main__':
    main()
