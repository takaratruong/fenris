# Lightweight Imitation Learning Methods for Small Robot Datasets

**Research Summary** | Thread: thr_32237b266d07 | Task: tsk_46b711bdd2c4  
**Requester:** Takara Proxy | **Date:** 2026-04-12

---

## Executive Summary

This document compares 2-3 lightweight imitation learning methods suitable for:
- Small robot demonstration datasets (10-200 demos)
- Single-GPU training budget (~8-24GB VRAM)
- Fast iteration cycles for robotics research

**Recommendation:** Start with **ACT (Action Chunking with Transformers)** for small datasets due to its sample efficiency and proven results on fine manipulation tasks.

---

## Methods Compared

### 1. ACT (Action Chunking with Transformers) ⭐ RECOMMENDED

**Source:** Zhao et al., "Learning Fine-Grained Bimanual Manipulation with Low-Cost Hardware" (RSS 2023)

**Architecture:**
- CVAE encoder + Transformer decoder
- Predicts action "chunks" (sequences of k=100 actions) rather than single steps
- Temporal ensembling for smooth execution

**Requirements:**
| Metric | Value |
|--------|-------|
| Demo count | 10-50 demonstrations |
| VRAM | ~8 GB |
| Training time | 1-4 hours |
| Inference | Real-time capable |

**Strengths:**
- Exceptional sample efficiency: 80-90% success with only 10 minutes of demonstrations
- Handles multimodal action distributions via CVAE
- Action chunking reduces compounding errors
- Open-source implementation: https://github.com/tonyzhaozh/act

**Weaknesses:**
- Requires careful tuning of chunk size k
- May struggle with very long-horizon tasks
- CVAE training can be unstable without proper KL annealing

**Best For:** Precise bimanual manipulation, assembly tasks, fine motor control

---

### 2. Diffusion Policy

**Source:** Chi et al., "Diffusion Policy: Visuomotor Policy Learning via Action Diffusion" (RSS 2023)

**Architecture:**
- Denoising diffusion model over action sequences
- Conditions on visual observations (typically from wrist cameras)
- Predicts action trajectories via iterative denoising

**Requirements:**
| Metric | Value |
|--------|-------|
| Demo count | 50-200 demonstrations |
| VRAM | ~12-16 GB |
| Training time | 4-12 hours |
| Inference | ~10Hz with DDIM (50 steps) |

**Strengths:**
- Excellent at capturing multimodal action distributions
- +47% average improvement over explicit policy baselines in benchmarks
- Handles contact-rich tasks well
- Strong generalization to visual variations

**Weaknesses:**
- Higher compute requirements than ACT
- Slower inference (though DDIM acceleration helps)
- Needs more demonstrations for stable training

**Best For:** Tasks with multimodal solutions, contact-rich manipulation, when data is more abundant

---

### 3. Simple Behavior Cloning + Action Chunking

**Architecture:**
- Standard MLP or small Transformer
- Predicts action chunks (like ACT) but without CVAE
- Often uses L2 loss with optional action smoothness regularization

**Requirements:**
| Metric | Value |
|--------|-------|
| Demo count | 10-100 demonstrations |
| VRAM | ~4 GB |
| Training time | < 1 hour |
| Inference | Real-time |

**Strengths:**
- Simplest implementation
- Fastest training iteration
- Good baseline for rapid prototyping
- Easy to debug and understand

**Weaknesses:**
- Cannot handle multimodal action distributions (averages modes)
- More prone to compounding errors
- Requires more careful data curation

**Best For:** Quick prototyping, simple pick-and-place, unimodal action distributions

---

## Comparison Table

| Method | Demos Needed | VRAM | Training | Multimodal | Complexity |
|--------|-------------|------|----------|------------|------------|
| **ACT** | 10-50 | 8 GB | 1-4 hr | ✓ (CVAE) | Medium |
| Diffusion Policy | 50-200 | 12-16 GB | 4-12 hr | ✓✓ | High |
| Simple BC + Chunk | 10-100 | 4 GB | < 1 hr | ✗ | Low |

---

## Practical Recommendations

### For a new project with ~20 demos:
1. **Start with ACT** - best sample efficiency
2. Use action chunk size k=100 for typical manipulation
3. Train for ~2000 epochs, monitor validation loss

### For tasks with multiple valid solutions:
1. **Use Diffusion Policy** if you have 100+ demos
2. **Use ACT** if demos are limited (CVAE helps with multimodality)

### For rapid prototyping:
1. **Start with Simple BC + Chunking**
2. Establish baseline performance quickly
3. Upgrade to ACT/Diffusion if needed

---

## Key Implementation Notes

### Data Collection
- Aim for 10-20 demos minimum for ACT
- Use teleoperation (not kinesthetic teaching) for consistency
- Include varied initial conditions
- Record at 10-50 Hz depending on task dynamics

### Training Tips
- Use image augmentation (random crops, color jitter)
- Normalize actions to [-1, 1] range
- Use exponential moving average of weights for evaluation
- Early stopping based on validation loss, not training loss

### Common Pitfalls
- Overfitting with very small datasets (use augmentation)
- Action discontinuities at chunk boundaries (use temporal ensembling)
- Camera calibration errors affecting generalization

---

## References

1. Zhao et al., "Learning Fine-Grained Bimanual Manipulation with Low-Cost Hardware" (RSS 2023)
2. Chi et al., "Diffusion Policy: Visuomotor Policy Learning via Action Diffusion" (RSS 2023)
3. Florence et al., "Implicit Behavioral Cloning" (CoRL 2021)
4. Mobile ALOHA / ALOHA 2 follow-up work for extended results

---

*Research compiled by the research agent for Takara Proxy's robotics project.*
