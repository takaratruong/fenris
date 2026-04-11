# Synthetic Shapes vs MNIST: Baseline Comparison

## Summary

**Synthetic shapes win as the simplest no-download baseline.** Zero external dependencies, faster training convergence, cleaner sample quality.

## Side-by-Side

| Metric | Synthetic Shapes | MNIST |
|--------|------------------|-------|
| **Download required** | ❌ None | ✅ ~60MB |
| **Image size** | 28×28 grayscale | 28×28 grayscale |
| **Dataset generation** | Instant (numpy) | Download + extract |
| **Training time (5 min)** | 511 epochs, 23993 steps | ~50 epochs |
| **Final loss** | 0.011 | ~0.02-0.03 |
| **Code complexity** | 114 lines | 268 lines |
| **Sample clarity** | Sharp edges, clean shapes | Blurry digits |
| **Classes** | 3 (circle, square, triangle) | 10 digits |

## Sample Quality

### Synthetic Shapes (after 5 min training)
- Clear triangles with defined edges
- Smooth circles
- Sharp-cornered squares
- Virtually no artifacts

### MNIST (equivalent training)
- Recognizable but blurry digit forms
- Noisy backgrounds
- Less defined edges

## Recommendation

For **diffusion model validation and experimentation**, use synthetic shapes:
1. No network dependency
2. Faster iteration (simpler distribution)
3. Easier to visually verify quality
4. Smaller codebase to maintain

MNIST remains useful for:
1. Benchmarking against published results
2. Testing with real-world data complexity
3. Multi-class generation validation

## Artifacts

- `shapes_ddpm.py` - 114-line implementation
- `model.pt` - Trained weights (~1MB)
- `samples_grid.png` - 16 generated samples
