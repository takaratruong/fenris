#!/usr/bin/env python3
"""
Minimal Flow Matching / Rectified Flow Implementation for 2D Gaussian Data.

Flow matching learns a velocity field v(x, t) that transports samples from
a simple prior p_0 (standard Gaussian) to the data distribution p_1.

The key insight of rectified flow: we can directly regress the velocity
connecting x_0 ~ N(0,I) to x_1 ~ data along a straight line interpolation:
    x_t = (1-t)*x_0 + t*x_1
    v_target = x_1 - x_0

Loss: MSE between predicted velocity v_theta(x_t, t) and target velocity (x_1 - x_0).

This is simpler than DDPM because:
1. No noise schedule to tune
2. Direct velocity regression 
3. ODE sampling (deterministic) vs SDE

References:
- Flow Matching for Generative Modeling (Lipman et al., 2023)
- Rectified Flow (Liu et al., 2022)
"""

import torch
import torch.nn as nn
import numpy as np
import time
import json
import os
from pathlib import Path
from datetime import datetime


class VelocityNetwork(nn.Module):
    """
    Tiny MLP network for velocity prediction.
    Architecture matches DDPM lane for fair comparison:
    Input: [x (2D), t (1D)] -> hidden layers -> output (2D velocity)
    """
    def __init__(self, input_dim=2, hidden_dim=128, num_layers=3):
        super().__init__()
        
        # Time embedding (sinusoidal, same as DDPM)
        self.time_embed_dim = 32
        
        layers = []
        # First layer: input + time embedding
        layers.append(nn.Linear(input_dim + self.time_embed_dim, hidden_dim))
        layers.append(nn.SiLU())
        
        # Hidden layers
        for _ in range(num_layers - 1):
            layers.append(nn.Linear(hidden_dim, hidden_dim))
            layers.append(nn.SiLU())
        
        # Output layer
        layers.append(nn.Linear(hidden_dim, input_dim))
        
        self.net = nn.Sequential(*layers)
    
    def time_embedding(self, t):
        """Sinusoidal time embedding."""
        half_dim = self.time_embed_dim // 2
        freqs = torch.exp(
            -np.log(10000) * torch.arange(half_dim, device=t.device) / half_dim
        )
        args = t[:, None] * freqs[None, :]
        return torch.cat([torch.sin(args), torch.cos(args)], dim=-1)
    
    def forward(self, x, t):
        """
        Predict velocity at position x and time t.
        
        Args:
            x: [batch, 2] position
            t: [batch] time in [0, 1]
        Returns:
            v: [batch, 2] predicted velocity
        """
        t_emb = self.time_embedding(t)
        inp = torch.cat([x, t_emb], dim=-1)
        return self.net(inp)


class FlowMatchingTrainer:
    """
    Flow Matching trainer for 2D data.
    
    Uses rectified flow formulation:
    - Linear interpolation: x_t = (1-t)*x_0 + t*x_1
    - Target velocity: v = x_1 - x_0
    - Loss: MSE(v_theta(x_t, t), v)
    """
    
    def __init__(self, model, lr=1e-3, device='cpu'):
        self.model = model.to(device)
        self.device = device
        self.optimizer = torch.optim.Adam(model.parameters(), lr=lr)
        
    def compute_loss(self, x1):
        """
        Compute flow matching loss.
        
        Args:
            x1: [batch, 2] data samples (target distribution)
        Returns:
            loss: scalar MSE loss
        """
        batch_size = x1.shape[0]
        
        # Sample from prior (standard Gaussian)
        x0 = torch.randn_like(x1)
        
        # Sample time uniformly in [0, 1]
        t = torch.rand(batch_size, device=self.device)
        
        # Linear interpolation: x_t = (1-t)*x_0 + t*x_1
        t_expand = t[:, None]
        x_t = (1 - t_expand) * x0 + t_expand * x1
        
        # Target velocity (direction from x_0 to x_1)
        v_target = x1 - x0
        
        # Predicted velocity
        v_pred = self.model(x_t, t)
        
        # MSE loss
        loss = ((v_pred - v_target) ** 2).mean()
        
        return loss
    
    def train_step(self, x1):
        """Single training step."""
        self.optimizer.zero_grad()
        loss = self.compute_loss(x1)
        loss.backward()
        self.optimizer.step()
        return loss.item()


class FlowMatchingSampler:
    """
    ODE sampler for flow matching models.
    
    Integrates the learned velocity field from t=0 to t=1:
    dx/dt = v_theta(x, t)
    Starting from x_0 ~ N(0, I)
    """
    
    def __init__(self, model, device='cpu'):
        self.model = model.to(device)
        self.device = device
        
    @torch.no_grad()
    def sample(self, n_samples, n_steps=100):
        """
        Generate samples using Euler integration.
        
        Args:
            n_samples: number of samples to generate
            n_steps: number of integration steps
        Returns:
            samples: [n_samples, 2] generated samples
        """
        self.model.eval()
        
        # Start from prior
        x = torch.randn(n_samples, 2, device=self.device)
        
        # Time steps from 0 to 1
        dt = 1.0 / n_steps
        
        for i in range(n_steps):
            t = torch.full((n_samples,), i * dt, device=self.device)
            v = self.model(x, t)
            x = x + v * dt
        
        return x


def create_2d_gaussian_mixture(n_samples, n_modes=8, std=0.1):
    """
    Create 2D Gaussian mixture data (ring of Gaussians).
    Standard benchmark for generative models.
    """
    # Modes arranged in a circle
    angles = np.linspace(0, 2 * np.pi, n_modes, endpoint=False)
    centers = np.stack([np.cos(angles), np.sin(angles)], axis=1) * 2.0
    
    # Sample mode indices
    mode_idx = np.random.randint(0, n_modes, n_samples)
    
    # Sample from Gaussians centered at modes
    samples = centers[mode_idx] + np.random.randn(n_samples, 2) * std
    
    return torch.tensor(samples, dtype=torch.float32)


def benchmark_training(n_epochs=1000, batch_size=256, n_data=10000, device='cpu'):
    """
    Benchmark flow matching training.
    
    Returns:
        dict with training metrics
    """
    print(f"Flow Matching Benchmark")
    print(f"Device: {device}")
    print(f"Epochs: {n_epochs}, Batch size: {batch_size}")
    print("-" * 50)
    
    # Create model
    model = VelocityNetwork(input_dim=2, hidden_dim=128, num_layers=3)
    trainer = FlowMatchingTrainer(model, lr=1e-3, device=device)
    
    # Create dataset
    data = create_2d_gaussian_mixture(n_data).to(device)
    
    # Training loop
    losses = []
    start_time = time.time()
    
    for epoch in range(n_epochs):
        # Random batch
        idx = torch.randint(0, n_data, (batch_size,))
        batch = data[idx]
        
        loss = trainer.train_step(batch)
        losses.append(loss)
        
        if (epoch + 1) % 100 == 0:
            print(f"Epoch {epoch+1}/{n_epochs}, Loss: {loss:.6f}")
    
    training_time = time.time() - start_time
    
    # Generate samples
    sampler = FlowMatchingSampler(model, device=device)
    sample_start = time.time()
    samples = sampler.sample(1000, n_steps=100)
    sample_time = time.time() - sample_start
    
    results = {
        "method": "flow_matching",
        "n_epochs": n_epochs,
        "batch_size": batch_size,
        "training_time_seconds": training_time,
        "final_loss": losses[-1],
        "mean_loss_last_100": np.mean(losses[-100:]),
        "sample_time_1000": sample_time,
        "samples_per_second": 1000 / sample_time,
        "device": str(device),
        "model_params": sum(p.numel() for p in model.parameters()),
    }
    
    print("-" * 50)
    print(f"Training time: {training_time:.2f}s")
    print(f"Final loss: {losses[-1]:.6f}")
    print(f"Sample generation (1000 samples): {sample_time:.3f}s")
    print(f"Model parameters: {results['model_params']}")
    
    return results, model, samples.cpu().numpy(), losses


def save_results(output_dir, results, samples, losses):
    """Save benchmark results and samples."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Save metrics
    with open(output_dir / "metrics.json", "w") as f:
        json.dump(results, f, indent=2)
    
    # Save samples
    np.save(output_dir / "samples.npy", samples)
    
    # Save loss curve
    np.save(output_dir / "losses.npy", np.array(losses))
    
    print(f"Results saved to {output_dir}")


def main():
    """Run flow matching benchmark."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Flow Matching 2D Benchmark")
    parser.add_argument("--epochs", type=int, default=1000, help="Training epochs")
    parser.add_argument("--batch-size", type=int, default=256, help="Batch size")
    parser.add_argument("--device", type=str, default="cpu", help="Device (cpu/cuda)")
    parser.add_argument("--output", type=str, default="./results", help="Output directory")
    args = parser.parse_args()
    
    device = args.device
    if device == "cuda" and not torch.cuda.is_available():
        print("CUDA not available, falling back to CPU")
        device = "cpu"
    
    results, model, samples, losses = benchmark_training(
        n_epochs=args.epochs,
        batch_size=args.batch_size,
        device=device
    )
    
    # Create timestamped output directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output) / f"flow_matching_{timestamp}"
    
    save_results(output_dir, results, samples, losses)
    
    # Save model
    torch.save(model.state_dict(), output_dir / "model.pt")
    
    return results


if __name__ == "__main__":
    main()
