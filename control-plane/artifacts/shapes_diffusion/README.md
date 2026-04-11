# Shapes Diffusion - Training Report

## Task Summary
Implemented and trained a minimal DDPM (Denoising Diffusion Probabilistic Model) on 28x28 synthetic shapes.

## Results

| Metric | Value |
|--------|-------|
| Training Steps | 23,993 |
| Epochs | 511 |
| Final Loss | 0.0111 |
| Training Time | 300s (5 min) |
| Device | CUDA |
| Code Lines | 114 (<200 ✓) |

## Architecture
- **Model**: Tiny 2-block U-Net
  - Encoder: 2 ConvBlocks with pooling
  - Bottleneck: 1 ConvBlock  
  - Decoder: 2 ConvBlocks with skip connections
  - Base channels: 32
  - Time embedding: 64-dim MLP
- **Diffusion**: Linear beta schedule, T=200 timesteps

## Dataset
- 3,000 synthetic shapes (circles, squares, triangles)
- 28x28 grayscale images
- Generated procedurally with numpy

## Artifacts
- `shapes_ddpm.py` - Implementation (114 lines)
- `model.pt` - Trained model weights (~1MB)
- `samples_grid.png` - 16-sample visualization
- `training_info.json` - Training metrics

## Sample Quality
Generated samples show clear, recognizable shapes:
- Triangles with clean edges
- Circles with smooth boundaries
- Squares with sharp corners

The model successfully learned the distribution of simple 2D shapes.
