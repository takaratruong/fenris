#!/usr/bin/env python3
"""
Minimal Flow Matching for 2D Data (Swiss Roll)

Flow matching uses velocity field regression - simpler than score matching.
We learn v(x, t) that transports noise to data.

ODE: dx/dt = v(x, t) from t=0 (noise) to t=1 (data)
Training: regress v_theta(x_t, t) toward (x_1 - x_0) for linear interpolation x_t = (1-t)*x_0 + t*x_1
"""

import torch
import torch.nn as nn
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter
from sklearn.datasets import make_swiss_roll
import os

# Set device
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
print(f"Using device: {device}")

# ============ DATA ============
def generate_swiss_roll(n_samples=5000):
    """Generate Swiss roll data scaled to [-1, 1]."""
    X, _ = make_swiss_roll(n_samples, noise=0.5)
    # Use x and z coordinates (the spiral plane)
    X = X[:, [0, 2]]
    # Normalize to [-1, 1]
    X = (X - X.mean(axis=0)) / (X.std(axis=0) * 2)
    return torch.tensor(X, dtype=torch.float32)

# ============ MODEL ============
class VelocityMLP(nn.Module):
    """Simple MLP for velocity field v(x, t)."""
    def __init__(self, hidden_dim=128, n_layers=3):
        super().__init__()
        layers = []
        # Input: 2 (position) + 1 (time) = 3
        in_dim = 3
        for i in range(n_layers):
            out_dim = hidden_dim if i < n_layers - 1 else 2
            layers.append(nn.Linear(in_dim, out_dim))
            if i < n_layers - 1:
                layers.append(nn.SiLU())
            in_dim = hidden_dim
        self.net = nn.Sequential(*layers)
    
    def forward(self, x, t):
        """
        x: (batch, 2) position
        t: (batch, 1) or (batch,) time
        """
        if t.dim() == 1:
            t = t.unsqueeze(-1)
        xt = torch.cat([x, t], dim=-1)
        return self.net(xt)

# ============ FLOW MATCHING ============
def sample_conditional_flow(x1, batch_size):
    """
    Sample from conditional flow path: x_t = (1-t)*x_0 + t*x_1
    where x_0 ~ N(0, I) and x_1 ~ data
    
    Returns: x_t, t, target_velocity
    """
    # Sample noise (source)
    x0 = torch.randn(batch_size, 2, device=device)
    
    # Sample random data points (target)
    idx = torch.randint(0, len(x1), (batch_size,))
    x1_batch = x1[idx].to(device)
    
    # Sample time uniformly
    t = torch.rand(batch_size, device=device)
    
    # Interpolate: x_t = (1-t)*x_0 + t*x_1
    t_expanded = t.unsqueeze(-1)
    x_t = (1 - t_expanded) * x0 + t_expanded * x1_batch
    
    # Target velocity for linear interpolation: v = x_1 - x_0
    target_v = x1_batch - x0
    
    return x_t, t, target_v

def train_flow_matching(model, data, n_steps=5000, batch_size=256, lr=1e-3):
    """Train flow matching model."""
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    losses = []
    
    model.train()
    for step in range(n_steps):
        # Sample from conditional flow
        x_t, t, target_v = sample_conditional_flow(data, batch_size)
        
        # Predict velocity
        pred_v = model(x_t, t)
        
        # MSE loss on velocity
        loss = ((pred_v - target_v) ** 2).mean()
        
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        
        losses.append(loss.item())
        
        if step % 500 == 0 or step == n_steps - 1:
            print(f"Step {step:5d} | Loss: {loss.item():.6f}")
    
    return losses

# ============ SAMPLING ============
@torch.no_grad()
def sample_ode(model, n_samples=1000, n_steps=100):
    """
    Sample by integrating ODE: dx/dt = v(x, t) from t=0 to t=1
    Using Euler method.
    """
    model.eval()
    
    # Start from noise at t=0
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
def create_flow_gif(trajectory, data, output_path, fps=20):
    """Create GIF showing flow from noise to data."""
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    
    data_np = data.numpy()
    
    def update(frame):
        for ax in axes:
            ax.clear()
        
        # Left: current state
        axes[0].scatter(trajectory[frame][:, 0], trajectory[frame][:, 1], 
                       alpha=0.5, s=1, c='blue')
        axes[0].set_xlim(-3, 3)
        axes[0].set_ylim(-3, 3)
        axes[0].set_title(f't = {frame / (len(trajectory)-1):.2f}')
        axes[0].set_aspect('equal')
        
        # Middle: target data
        axes[1].scatter(data_np[:, 0], data_np[:, 1], alpha=0.3, s=1, c='green')
        axes[1].set_xlim(-3, 3)
        axes[1].set_ylim(-3, 3)
        axes[1].set_title('Target Data (Swiss Roll)')
        axes[1].set_aspect('equal')
        
        # Right: overlay comparison
        axes[2].scatter(data_np[:, 0], data_np[:, 1], alpha=0.2, s=1, c='green', label='Target')
        axes[2].scatter(trajectory[frame][:, 0], trajectory[frame][:, 1], 
                       alpha=0.5, s=1, c='blue', label='Generated')
        axes[2].set_xlim(-3, 3)
        axes[2].set_ylim(-3, 3)
        axes[2].set_title('Comparison')
        axes[2].set_aspect('equal')
        if frame == 0:
            axes[2].legend(markerscale=5)
        
        plt.tight_layout()
        return axes
    
    # Sample fewer frames for GIF
    n_frames = min(50, len(trajectory))
    frame_indices = np.linspace(0, len(trajectory) - 1, n_frames, dtype=int)
    
    anim = FuncAnimation(fig, update, frames=frame_indices, blit=False)
    anim.save(output_path, writer=PillowWriter(fps=fps))
    plt.close()
    print(f"Saved GIF to {output_path}")

def plot_training_loss(losses, output_path):
    """Plot training loss curve."""
    plt.figure(figsize=(10, 4))
    plt.plot(losses)
    plt.xlabel('Step')
    plt.ylabel('Loss')
    plt.title('Flow Matching Training Loss')
    plt.yscale('log')
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()
    print(f"Saved loss plot to {output_path}")

def plot_final_comparison(trajectory, data, output_path):
    """Plot final generated samples vs data."""
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    
    data_np = data.numpy()
    
    # Initial noise
    axes[0].scatter(trajectory[0][:, 0], trajectory[0][:, 1], alpha=0.5, s=2, c='blue')
    axes[0].set_xlim(-3, 3)
    axes[0].set_ylim(-3, 3)
    axes[0].set_title('Initial Noise (t=0)')
    axes[0].set_aspect('equal')
    
    # Final generated
    axes[1].scatter(trajectory[-1][:, 0], trajectory[-1][:, 1], alpha=0.5, s=2, c='blue')
    axes[1].set_xlim(-3, 3)
    axes[1].set_ylim(-3, 3)
    axes[1].set_title('Generated (t=1)')
    axes[1].set_aspect('equal')
    
    # Target
    axes[2].scatter(data_np[:, 0], data_np[:, 1], alpha=0.3, s=2, c='green')
    axes[2].set_xlim(-3, 3)
    axes[2].set_ylim(-3, 3)
    axes[2].set_title('Target Data')
    axes[2].set_aspect('equal')
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()
    print(f"Saved comparison plot to {output_path}")

# ============ MAIN ============
def main():
    import time
    start_time = time.time()
    
    # Output directory
    output_dir = '/home/ubuntu/.openclaw/workspace/control-plane/artifacts/flow_matching'
    os.makedirs(output_dir, exist_ok=True)
    
    print("=" * 50)
    print("Flow Matching for 2D Swiss Roll Data")
    print("=" * 50)
    
    # Generate data
    print("\n[1] Generating Swiss Roll data...")
    data = generate_swiss_roll(n_samples=5000)
    print(f"    Data shape: {data.shape}")
    
    # Create model
    print("\n[2] Creating velocity MLP...")
    model = VelocityMLP(hidden_dim=128, n_layers=3).to(device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"    Parameters: {n_params:,}")
    
    # Train
    print("\n[3] Training flow matching...")
    losses = train_flow_matching(model, data, n_steps=5000, batch_size=256, lr=1e-3)
    
    # Sample
    print("\n[4] Sampling via ODE integration...")
    trajectory = sample_ode(model, n_samples=2000, n_steps=100)
    print(f"    Generated {len(trajectory[-1])} samples")
    
    # Visualize
    print("\n[5] Creating visualizations...")
    plot_training_loss(losses, f"{output_dir}/training_loss.png")
    plot_final_comparison(trajectory, data, f"{output_dir}/final_comparison.png")
    create_flow_gif(trajectory, data, f"{output_dir}/flow_matching.gif", fps=15)
    
    elapsed = time.time() - start_time
    print(f"\n{'=' * 50}")
    print(f"Completed in {elapsed:.1f} seconds ({elapsed/60:.1f} minutes)")
    print(f"Artifacts saved to: {output_dir}")
    print(f"{'=' * 50}")
    
    return output_dir

if __name__ == "__main__":
    main()
