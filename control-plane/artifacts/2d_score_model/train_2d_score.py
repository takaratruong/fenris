#!/usr/bin/env python3
"""Minimal score-based diffusion for 2D Swiss roll point cloud."""
import numpy as np
import torch
import torch.nn as nn
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter
from sklearn.datasets import make_swiss_roll
import time

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Using device: {device}")

# Generate Swiss roll data (2D projection)
def get_swiss_roll(n_samples=2000):
    X, _ = make_swiss_roll(n_samples, noise=0.5)
    X = X[:, [0, 2]]  # Take x and z coordinates for 2D
    X = (X - X.mean(axis=0)) / X.std(axis=0)  # Normalize
    return torch.tensor(X, dtype=torch.float32, device=device)

# MLP Score Model: 3 hidden layers, 64 units each
class ScoreModel(nn.Module):
    def __init__(self, dim=2, hidden=64, n_layers=3):
        super().__init__()
        layers = [nn.Linear(dim + 1, hidden), nn.SiLU()]  # +1 for time embedding
        for _ in range(n_layers - 1):
            layers += [nn.Linear(hidden, hidden), nn.SiLU()]
        layers.append(nn.Linear(hidden, dim))
        self.net = nn.Sequential(*layers)
    
    def forward(self, x, t):
        t_embed = t.unsqueeze(-1) if t.dim() == 1 else t
        return self.net(torch.cat([x, t_embed], dim=-1))

# Noise schedule
def get_sigma(t):
    return 0.1 + t * (25.0 - 0.1)  # Linear schedule from 0.1 to 25

# Training: Denoising score matching
def train(model, data, n_steps=5000, lr=1e-3, batch_size=256):
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    losses = []
    start = time.time()
    
    for step in range(n_steps):
        idx = torch.randint(0, len(data), (batch_size,))
        x = data[idx]
        t = torch.rand(batch_size, device=device)
        sigma = get_sigma(t).unsqueeze(-1)
        noise = torch.randn_like(x)
        x_noisy = x + sigma * noise
        
        # Score target: -noise/sigma (gradient of log p(x_noisy|x))
        target = -noise / sigma
        pred = model(x_noisy, t)
        loss = ((pred - target) ** 2).mean()
        
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        losses.append(loss.item())
        
        if time.time() - start > 120:  # 2 minute limit
            print(f"Time limit reached at step {step}")
            break
        if step % 500 == 0:
            print(f"Step {step}, Loss: {loss.item():.4f}")
    
    return losses

# Sampling via Euler-Maruyama (reverse SDE)
@torch.no_grad()
def sample(model, n_samples=500, n_steps=100):
    trajectory = []
    x = torch.randn(n_samples, 2, device=device) * get_sigma(torch.tensor(1.0))
    trajectory.append(x.cpu().numpy())
    
    dt = 1.0 / n_steps
    for i in range(n_steps):
        t = 1.0 - i * dt
        t_tensor = torch.full((n_samples,), t, device=device)
        sigma = get_sigma(t_tensor).unsqueeze(-1)
        
        score = model(x, t_tensor)
        drift = -sigma ** 2 * score
        noise = torch.randn_like(x) * sigma * np.sqrt(dt) if i < n_steps - 1 else 0
        x = x - drift * dt + noise
        
        if i % 5 == 0:
            trajectory.append(x.cpu().numpy())
    
    trajectory.append(x.cpu().numpy())
    return trajectory

# Create animation
def make_animation(trajectory, data, save_path):
    fig, ax = plt.subplots(figsize=(8, 8))
    data_np = data.cpu().numpy()
    
    def update(frame):
        ax.clear()
        ax.scatter(data_np[:, 0], data_np[:, 1], alpha=0.3, s=5, c='blue', label='Data')
        ax.scatter(trajectory[frame][:, 0], trajectory[frame][:, 1], 
                   alpha=0.7, s=10, c='red', label='Generated')
        ax.set_xlim(-4, 4)
        ax.set_ylim(-4, 4)
        ax.set_title(f'Denoising Step {frame}/{len(trajectory)-1}')
        ax.legend()
        ax.set_aspect('equal')
    
    anim = FuncAnimation(fig, update, frames=len(trajectory), interval=100)
    anim.save(save_path, writer=PillowWriter(fps=10))
    plt.close()
    print(f"Animation saved to {save_path}")

if __name__ == "__main__":
    print("Generating Swiss roll data...")
    data = get_swiss_roll(2000)
    
    print("Creating score model...")
    model = ScoreModel(dim=2, hidden=64, n_layers=3).to(device)
    param_count = sum(p.numel() for p in model.parameters())
    print(f"Model parameters: {param_count}")
    
    print("Training (max 2 minutes)...")
    losses = train(model, data, n_steps=10000, lr=1e-3, batch_size=256)
    
    print("Generating samples...")
    trajectory = sample(model, n_samples=500, n_steps=100)
    
    print("Creating animation...")
    make_animation(trajectory, data, "denoising_trajectory.gif")
    
    # Save loss plot
    plt.figure(figsize=(10, 4))
    plt.plot(losses)
    plt.xlabel('Step')
    plt.ylabel('Loss')
    plt.title('Training Loss')
    plt.savefig('training_loss.png')
    plt.close()
    print("Loss plot saved to training_loss.png")
    
    print("Done!")
