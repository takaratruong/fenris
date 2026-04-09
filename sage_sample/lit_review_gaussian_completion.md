# Literature Review: 3D Object Completion for Gaussian Splats

## Problem Statement

**Goal**: Complete missing geometry on isolated objects extracted from gaussian splat scenes — specifically, occluded regions like object bottoms that contacted floors/walls.

**Why it's hard**: Unlike scene inpainting (filling holes after removing objects), we need to:
1. Hallucinate geometry that was never captured
2. Generate consistent gaussian attributes (position, color, opacity, scale, rotation)
3. Work with isolated objects, not full scenes with camera parameters

---

## 1. Scene-Level Gaussian Inpainting (Not Our Problem, But Related)

These methods fill holes *within scenes* after removing objects — not completing isolated objects.

### GaussianEditor / GScream / InFusion
- Remove an object from a scene, then inpaint the background
- Require original camera poses and full scene context
- **Limitation**: Designed for "erasing," not "completing"

### RI3D (NVIDIA, 2024)
- **Paper**: "RI3D: Few-View 3D Reconstruction with Repair and Inpainting Diffusion Priors"
- Uses diffusion models to repair corrupted/incomplete NeRF/3DGS scenes
- **Status**: Code "coming soon" — not yet released
- **Limitation**: Scene-level, requires multi-view images

### EditSplat (CVPR 2025)
- Text-guided 3D gaussian editing with multi-view consistency
- Can add/modify content, but not complete occluded regions of extracted objects

---

## 2. Point Cloud Completion (Most Relevant Prior Work)

These methods complete partial point clouds — could provide geometry for our problem.

### PoinTr (ICCV 2021)
- **GitHub**: yuxumin/PoinTr
- Transformer-based, set-to-set prediction
- Trained on ShapeNet (synthetic CAD models)
- **Limitation**: Outputs geometry only, no color/appearance

### SDFusion (CVPR 2023)
- **GitHub**: yccyenchicheng/SDFusion
- Latent diffusion on SDF volumes
- Handles partial TSDF → complete SDF
- Multi-modal conditioning (text, image, partial shape)
- **Limitation**: Outputs SDF, not gaussians; synthetic training

### SDS-Complete (NeurIPS 2023)
- **Website**: sds-complete.github.io
- Uses pretrained text-to-image diffusion (Stable Diffusion) as prior
- Test-time optimization: fits SDF surface constrained to pass through observed points
- Works on real-world scans (Redwood, KITTI LiDAR)
- **Key insight**: Leverage 2D diffusion priors for 3D completion
- **Limitation**: Slow (test-time optimization), outputs SDF not gaussians

### DiffComplete (NeurIPS 2023)
- Conditional diffusion with hierarchical fusion
- ~40% lower L1 error than prior methods
- Generalizes to unseen categories
- **Limitation**: Still outputs geometry only

### SC-Diff (ECCV 2024)
- 3D latent TSDF diffusion
- Single model handles all object classes
- Optional image conditioning via cross-attention

### PatchComplete (NeurIPS 2022)
- Multi-scale SDF patch priors
- Learns local geometric patterns that transfer across categories
- 19% lower Chamfer distance on synthetic, 9% on real scans

---

## 3. Single-Image/Sparse-View 3D Reconstruction

These generate complete 3D from images — could be used to "re-generate" the object.

### Zero123++ / One-2-3-45 / Wonder3D
- Generate novel views from single image, then reconstruct 3D
- **Limitation**: Require a clean input view; extracted splat objects are noisy

### 3D-Fixer (2025)
- In-place scene completion from single image
- Progressive coarse-to-fine with dual-branch conditioning
- New dataset: ARSG-110K (110K scenes, 3M images)
- **Limitation**: Designed for scene composition, not isolated objects

### SSR (3DV 2024)
- Single-view neural implicit shape + radiance field
- Recovers shape and texture simultaneously
- **Limitation**: Requires clean single-view input image

---

## 4. Object-Centric Transfer/Extraction

### TranSplat (2025)
- **Website**: tonyyu0822.github.io/transplat
- Cross-scene object transfer with relighting
- Uses spherical harmonic transfer for appearance
- **Relevant**: Addresses "precise 3D object extraction" as a sub-problem
- **Limitation**: Doesn't complete missing geometry

### 3DitScene (CVPR 2024)
- Language-guided disentangled gaussian splatting
- Object segmentation + manipulation
- **Limitation**: Editing, not completion

---

## 5. The Gap: Gaussian Splat Object Completion

**What exists:**
- Scene-level gaussian inpainting (assumes full scene + cameras)
- Point cloud completion (geometry only, no gaussian attributes)
- Single-image 3D (requires clean input image)

**What doesn't exist:**
- Direct gaussian splat completion for isolated objects
- Methods that output all gaussian attributes (pos, color, opacity, scale, rotation)
- Completion from partial gaussian splat objects without original cameras

---

## Proposed Approaches (Ordered by Practicality)

### A. Hybrid: Point Completion + Attribute Prediction
1. Extract positions from gaussians as point cloud
2. Run PoinTr/SDFusion to complete geometry
3. For new points, predict attributes by:
   - k-NN interpolation from existing gaussians
   - Small MLP trained on existing gaussians to predict (color, opacity, scale, rot) from position

**Pros**: Uses existing tools, no retraining needed
**Cons**: Attribute prediction may produce artifacts at boundaries

### B. SDS-Complete Adaptation for Gaussians
1. Represent object as gaussian splat (not SDF)
2. Use SDS loss from Stable Diffusion on rendered views
3. Add loss to keep existing gaussians fixed
4. Optimize new gaussians in missing regions

**Pros**: Leverages 2D priors for appearance, end-to-end gaussian output
**Cons**: Slow, may need careful tuning

### C. Diffusion on Gaussian Latent Space
1. Train autoencoder: gaussian splat object → latent code
2. Train diffusion model on latent space conditioned on partial input
3. Decode completed latent to full gaussian splat

**Pros**: Native gaussian output, could be fast at inference
**Cons**: Requires large dataset of gaussian splat objects + paired partial/complete examples

### D. Simple Heuristics (For Quick Results)
1. **Mirror/reflect** gaussians from visible regions to fill symmetric holes
2. **Extrude** floor-contact gaussians downward with interpolated colors
3. **Smooth extension** using Laplacian-like propagation

**Pros**: Fast, no ML needed
**Cons**: Only works for simple shapes, produces obvious artifacts

---

## Recommended Starting Point

For your case (chair bottom touching floor):

1. **Quick test**: Try mirror/extrusion heuristic first
2. **Better quality**: PoinTr for geometry + k-NN attribute interpolation
3. **Research direction**: Adapt SDS-Complete to gaussian splats

The full "diffusion on gaussian latent space" approach would be novel research — potentially a paper.

---

## Key Papers

| Paper | Year | Task | Output | Code |
|-------|------|------|--------|------|
| PoinTr | 2021 | Point completion | Points | ✅ yuxumin/PoinTr |
| PatchComplete | 2022 | SDF completion | SDF | ✅ |
| SDFusion | 2023 | Multi-modal completion | SDF | ✅ yccyenchicheng/SDFusion |
| SDS-Complete | 2023 | Point cloud completion | SDF | ✅ |
| DiffComplete | 2023 | Shape completion | SDF/Mesh | ✅ |
| SC-Diff | 2024 | TSDF completion | TSDF | ? |
| RI3D | 2024 | Scene repair | 3DGS | ❌ "coming soon" |
| 3D-Fixer | 2025 | Scene completion | Mesh | ? |
| TranSplat | 2025 | Object transfer | 3DGS | ? |

---

## Conclusion

**Object completion for isolated gaussian splats is an open problem.** The closest work:
- Point cloud completion → gives geometry, not full gaussians
- SDS-Complete → uses 2D priors, but outputs SDF not gaussians
- RI3D → would be ideal, but code not released

A practical pipeline today: PoinTr + attribute interpolation. A paper-worthy contribution: native gaussian splat completion using diffusion priors.
