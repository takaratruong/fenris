#!/usr/bin/env python3
"""
Gaussian Splat Object Segmentation and Manipulation
Segments gaussians by bounding boxes, allows per-object transforms.
"""

import json
import numpy as np
from plyfile import PlyData, PlyElement
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple
import copy


@dataclass
class BoundingBox:
    """Axis-aligned bounding box from 8 corner points"""
    min_xyz: np.ndarray  # [3]
    max_xyz: np.ndarray  # [3]
    
    @classmethod
    def from_corners(cls, corners: List[Dict]) -> 'BoundingBox':
        """Create from list of 8 corner dicts with x,y,z keys"""
        pts = np.array([[c['x'], c['y'], c['z']] for c in corners])
        return cls(
            min_xyz=pts.min(axis=0),
            max_xyz=pts.max(axis=0)
        )
    
    def contains(self, points: np.ndarray, margin: float = 0.0) -> np.ndarray:
        """Check which points are inside the box (with optional margin)"""
        return np.all(
            (points >= self.min_xyz - margin) & 
            (points <= self.max_xyz + margin),
            axis=1
        )
    
    @property
    def center(self) -> np.ndarray:
        return (self.min_xyz + self.max_xyz) / 2
    
    @property
    def size(self) -> np.ndarray:
        return self.max_xyz - self.min_xyz


@dataclass 
class SceneObject:
    """A segmented object from the gaussian splat"""
    ins_id: str
    label: str
    bbox: BoundingBox
    gaussian_indices: np.ndarray  # indices into original splat
    
    def __repr__(self):
        return f"SceneObject(id={self.ins_id}, label='{self.label}', n_gaussians={len(self.gaussian_indices)})"


class GaussianSplat:
    """Gaussian splat with object segmentation support"""
    
    def __init__(self, ply_path: str):
        self.ply_path = Path(ply_path)
        self.ply_data = PlyData.read(str(self.ply_path))
        self.vertex_data = self.ply_data['vertex'].data
        
        # Extract positions
        self.positions = np.stack([
            self.vertex_data['x'],
            self.vertex_data['y'], 
            self.vertex_data['z']
        ], axis=1).astype(np.float32)
        
        # Get all property names for later reconstruction
        self.property_names = [p.name for p in self.ply_data['vertex'].properties]
        
        print(f"Loaded {len(self.positions)} gaussians from {ply_path}")
        print(f"Position range: {self.positions.min(axis=0)} to {self.positions.max(axis=0)}")
    
    def segment_by_bbox(self, bbox: BoundingBox, margin: float = 0.01) -> np.ndarray:
        """Return indices of gaussians inside bounding box"""
        mask = bbox.contains(self.positions, margin=margin)
        return np.where(mask)[0]
    
    def segment_objects(self, labels_json: str, margin: float = 0.02) -> List[SceneObject]:
        """Segment splat into objects based on labels.json bounding boxes"""
        with open(labels_json, 'r') as f:
            labels = json.load(f)
        
        objects = []
        skipped = 0
        for item in labels:
            # Skip items without bounding boxes
            if 'bounding_box' not in item:
                skipped += 1
                continue
                
            bbox = BoundingBox.from_corners(item['bounding_box'])
            indices = self.segment_by_bbox(bbox, margin=margin)
            
            if len(indices) > 0:
                obj = SceneObject(
                    ins_id=item['ins_id'],
                    label=item['label'],
                    bbox=bbox,
                    gaussian_indices=indices
                )
                objects.append(obj)
        
        print(f"Segmented {len(objects)} objects with gaussians (skipped {skipped} without bboxes)")
        return objects
    
    def extract_object(self, obj: SceneObject) -> 'GaussianSplat':
        """Extract a single object as a new GaussianSplat"""
        # This creates a view - for actual extraction we need to copy data
        new_splat = copy.copy(self)
        new_splat.positions = self.positions[obj.gaussian_indices].copy()
        new_splat.vertex_data = self.vertex_data[obj.gaussian_indices].copy()
        return new_splat
    
    def transform_gaussians(
        self,
        indices: np.ndarray,
        translation: Optional[np.ndarray] = None,
        rotation: Optional[np.ndarray] = None,  # 3x3 rotation matrix
        pivot: Optional[np.ndarray] = None  # rotation pivot point
    ):
        """
        Apply rigid transform to subset of gaussians.
        Modifies positions in-place.
        
        For full gaussian transform, we'd also need to rotate covariances:
        Σ' = R @ Σ @ R.T
        But for visualization, position transform is usually enough.
        """
        if rotation is not None:
            if pivot is None:
                pivot = self.positions[indices].mean(axis=0)
            
            # Translate to pivot, rotate, translate back
            centered = self.positions[indices] - pivot
            rotated = (rotation @ centered.T).T
            self.positions[indices] = rotated + pivot
            
            # Update vertex data (structured array)
            self.vertex_data['x'][indices] = self.positions[indices, 0]
            self.vertex_data['y'][indices] = self.positions[indices, 1]
            self.vertex_data['z'][indices] = self.positions[indices, 2]
        
        if translation is not None:
            self.positions[indices] += translation
            self.vertex_data['x'][indices] += translation[0]
            self.vertex_data['y'][indices] += translation[1]
            self.vertex_data['z'][indices] += translation[2]
    
    def save(self, output_path: str):
        """Save modified splat to PLY file"""
        # Create new vertex element with modified data
        vertex_element = PlyElement.describe(
            self.vertex_data,
            'vertex'
        )
        
        # Preserve any other elements (like sh coefficients)
        elements = [vertex_element]
        for elem in self.ply_data.elements:
            if elem.name != 'vertex':
                elements.append(elem)
        
        ply = PlyData(elements)
        ply.write(output_path)
        print(f"Saved modified splat to {output_path}")


def rotation_matrix_z(angle_deg: float) -> np.ndarray:
    """Create rotation matrix around Z axis"""
    theta = np.radians(angle_deg)
    c, s = np.cos(theta), np.sin(theta)
    return np.array([
        [c, -s, 0],
        [s, c, 0],
        [0, 0, 1]
    ])


def rotation_matrix_y(angle_deg: float) -> np.ndarray:
    """Create rotation matrix around Y axis"""
    theta = np.radians(angle_deg)
    c, s = np.cos(theta), np.sin(theta)
    return np.array([
        [c, 0, s],
        [0, 1, 0],
        [-s, 0, c]
    ])


def demo():
    """Demo: segment objects and move one"""
    
    # Paths
    splat_path = "/home/ubuntu/datasets/sage_batch_walking/0001_839920_seq_006_fruit/gs_output_15k/point_cloud/iteration_15000/point_cloud.ply"
    labels_path = "/home/ubuntu/datasets/scenes/0001_839920/labels.json"
    output_path = "/home/ubuntu/.openclaw/workspace/modified_splat.ply"
    
    # Load splat
    splat = GaussianSplat(splat_path)
    
    # Segment objects
    objects = splat.segment_objects(labels_path, margin=0.03)
    
    # Print object summary
    print("\n=== Segmented Objects ===")
    label_counts = {}
    for obj in objects:
        label_counts[obj.label] = label_counts.get(obj.label, 0) + 1
    
    for label, count in sorted(label_counts.items(), key=lambda x: -x[1])[:20]:
        print(f"  {label}: {count} instances")
    
    # Find a specific object to move (e.g., a fruit or cup)
    target_label = "chair"
    target_objects = [o for o in objects if o.label == target_label]
    
    print(f"\n=== Objects by label ===")
    from collections import Counter
    label_counts = Counter(o.label for o in objects)
    for label, count in label_counts.most_common(10):
        sample = [o for o in objects if o.label == label][0]
        print(f"  {label}: {count} instances, sample has {len(sample.gaussian_indices)} gaussians")
    
    if target_objects:
        # Sort by number of gaussians, pick one with good coverage
        target_objects.sort(key=lambda o: len(o.gaussian_indices), reverse=True)
        obj = target_objects[0]  # Take the one with most gaussians
        
        print(f"\n=== Moving object: {obj} ===")
        print(f"  Original center: {splat.positions[obj.gaussian_indices].mean(axis=0)}")
        print(f"  Num gaussians: {len(obj.gaussian_indices)}")
        
        # Move it up and to the side
        translation = np.array([0.5, 0.3, 0.5])  # x, y, z offset
        splat.transform_gaussians(
            obj.gaussian_indices,
            translation=translation
        )
        
        print(f"  Translated by: {translation}")
        print(f"  New center: {splat.positions[obj.gaussian_indices].mean(axis=0)}")
        
        # Save modified splat
        splat.save(output_path)
        
        return splat, objects, obj
    else:
        print(f"No objects with label '{target_label}' found")
        return splat, objects, None


if __name__ == "__main__":
    splat, objects, moved_obj = demo()
