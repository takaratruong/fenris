#!/usr/bin/env python3
"""
Flow Matching for 2D Swiss Roll - Improved Version

Key improvements:
1. Sinusoidal time embeddings (like transformers)
2. Residual connections
3. More training steps
"""

import torch
import torch.nn as nn
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter
from sklearn.datasets import make_swiss_roll
import os
import time

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
print(f"Using device: {device}")

# ============ DATA ============
def generate_swiss_roll(n_samples=5000):
    """Generate Swiss roll scaled to reasonable range."""
    X, _ = make_swiss_roll(n_samples, noise=0.3)
    X = X[:, [0, 2]]  # Use x, z plane
    X = (X - X.mean(axis=0)) / X.std(axis=0)
    return torch.tensor(X, dtype=torch.float32)

# ============ MODEL ============
class SinusoidalEmbedding(nn.Module):
    """Sinusoidal time embedding."""
    def __init__(self, dim):
        super().__init__()
        self.dim = dim
    
    def forward(self, t):
        device = t.device
        half_dim = self.dim // 2
        emb = np.log(10000) / (half_dim - 1)
        emb = torch.exp(torch.arange(half_dim, device=device) * -emb)
        emb = t[:, None] * emb[None, :]
        emb = torch.cat([torch.sin(emb), torch.cos(emb)], dim=-1)
        return emb

class VelocityNet(nn.Module):
    """Velocity network with time conditioning."""
    def __init__(self, hidden_dim=128, time_dim=32):
        super().__init__()
        self.time_embed = SinusoidalEmbedding(time_dim)
        self.time_mlp = nn.Sequential(
            nn.Linear(time_dim, hidden_dim),
            nn.SiLU(),
            nn.Linear(hidden_dim, hidden_dim),
        )
        
        # Main network
        self.input_proj = nn.Linear(2, hidden_dim)
        
        self.blocks = nn.ModuleList([
            nn.Sequential(
                nn.Linear(hidden_dim, hidden_dim),
                nn.SiLU(),
                nn.Linear(hidden_dim, hidden_dim),
            ) for _ in range(3)
        ])
        
        self.output_proj = nn.Linear(hidden_dim, 2)
    
    def forward(self, x, t):
        # Time embedding
        t_emb = self.time_embed(t)
        t_emb = self.time_mlp(t_emb)
        
        # Process input
        h = self.input_proj(x)
        h = h + t_emb  # Add time conditioning
        
        # Residual blocks
        for block in self.blocks:
            h = h + block(h)
        
        return self.output_proj(h)

# ============ TRAINING ============
def train(model, data, n_steps=8000, batch_size=512, lr=3e-4):
    """Train flow matching model."""
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, n_steps)
    losses = []
    
    model.train()
    for step in range(n_steps):
        # Sample batch
        idx = torch.randint(0, len(data), (batch_size,))
        x1 = data[idx].to(device)  # Target (data)
        x0 = torch.randn_like(x1)   # Source (noise)
        
        # Random time
        t = torch.rand(batch_size, device=device)
        
        # Interpolate
        x_t = (1 - t[:, None]) * x0 + t[:, None] * x1
        
        # Target velocity (for linear path)
        v_target = x1 - x0
        
        # Predict
        v_pred = model(x_t, t)
        
        # Loss
        loss = ((v_pred - v_target) ** 2).mean()
        
        optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        scheduler.step()
        
        losses.append(loss.item())
        
        if step % 1000 == 0 or step == n_steps - 1:
            print(f"Step {step:5d} | Loss: {loss.item():.4f} | LR: {scheduler.get_last_lr()[0]:.2e}")
    
    return losses

# ============ SAMPLING ============
@torch.no_grad()
def sample(model, n_samples=2000, n_steps=100):
    """Sample by integrating ODE."""
    model.eval()
    x = torch.randn(n_samples, 2, device=device)
    dt = 1.0 / n_steps
    
    trajectory = [x.cpu().numpy()]
    for i in range(n_steps):
        t = torch.full((n_samples,), i * dt, device=device)
        v = model(x, t)
        x = x + v * dt
        trajectory.append(x.cpu().numpy())
    
    return trajectory

# ============ VISUALIZATION ============
def create_gif(trajectory, data, path, fps=20):
    """Create flow GIF."""
    fig, ax = plt.subplots(figsize=(8, 8))
    data_np = data.numpy()
    
    def update(frame):
        ax.clear()
        t = frame / (len(trajectory) - 1)
        
        # Plot target in background
        ax.scatter(data_np[:, 0], data_np[:, 1], alpha=0.15, s=3, c='green', label='Target')
        # Plot current samples
        ax.scatter(trajectory[frame][:, 0], trajectory[frame][:, 1], 
                  alpha=0.6, s=3, c='blue', label='Flow')
        
        ax.set_xlim(-3.5, 3.5)
        ax.set_ylim(-3.5, 3.5)
        ax.set_title(f'Flow Matching: t = {t:.2f}', fontsize=14)
        ax.set_aspect('equal')
        ax.legend(loc='upper right', markerscale=3)
        return ax,
    
    # Sample frames
    n_frames = min(60, len(trajectory))
    frames = np.linspace(0, len(trajectory) - 1, n_frames, dtype=int)
    
    anim = FuncAnimation(fig, update, frames=frames, blit=False)
    anim.save(path, writer=PillowWriter(fps=fps))
    plt.close()
    print(f"Saved: {path}")

def save_comparison(trajectory, data, path):
    """Save comparison plot."""
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    data_np = data.numpy()
    
    for ax, (samples, title) in zip(axes, [
        (trajectory[0], 'Noise (t=0)'),
        (trajectory[-1], 'Generated (t=1)'),
        (data_np, 'Target Data')
    ]):
        color = 'green' if 'Target' in title else 'blue'
        ax.scatter(samples[:, 0], samples[:, 1], alpha=0.5, s=3, c=color)
        ax.set_xlim(-3.5, 3.5)
        ax.set_ylim(-3.5, 3.5)
        ax.set_title(title, fontsize=12)
        ax.set_aspect('equal')
    
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"Saved: {path}")

# ============ MAIN ============
def main():
    start = time.time()
    out_dir = '/home/ubuntu/.openclaw/workspace/control-plane/artifacts/flow_matching'
    os.makedirs(out_dir, exist_ok=True)
    
    print("=" * 50)
    print("Flow Matching v2 - Swiss Roll")
    print("=" * 50)
    
    # Data
    print("\n[1] Generating data...")
    data = generate_swiss_roll(5000)
    
    # Model
    print("\n[2] Creating model...")
    model = VelocityNet(hidden_dim=128, time_dim=32).to(device)
    params = sum(p.numel() for p in model.parameters())
    print(f"    Parameters: {params:,}")
    
    # Train
    print("\n[3] Training...")
    losses = train(model, data, n_steps=8000, batch_size=512)
    
    # Sample
    print("\n[4] Sampling...")
    trajectory = sample(model, n_samples=2000, n_steps=100)
    
    # Visualize
    print("\n[5] Saving visualizations...")
    save_comparison(trajectory, data, f"{out_dir}/comparison_v2.png")
    create_gif(trajectory, data, f"{out_dir}/flow_v2.gif")
    
    # Loss plot
    plt.figure(figsize=(10, 4))
    plt.plot(losses)
    plt.xlabel('Step')
    plt.ylabel('Loss')
    plt.title('Training Loss')
    plt.yscale('log')
    plt.grid(True, alpha=0.3)
    plt.savefig(f"{out_dir}/loss_v2.png", dpi=150)
    plt.close()
    
    elapsed = time.time() - start
    print(f"\n{'=' * 50}")
    print(f"Done in {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"Artifacts: {out_dir}")
    print("=" * 50)

if __name__ == "__main__":
    main()
