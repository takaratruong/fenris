#!/usr/bin/env python3
"""
Minimal DDPM Implementation for 2D Gaussian Mixture Data.

This is the DDPM baseline to compare against Flow Matching.
Uses the same architecture and data as flow_matching.py for fair comparison.

DDPM learns to predict noise added at timestep t:
- Forward: q(x_t | x_0) = N(sqrt(alpha_bar_t) * x_0, (1 - alpha_bar_t) * I)
- Reverse: p(x_{t-1} | x_t) parameterized by noise prediction network

Key differences from Flow Matching:
1. Requires noise schedule (betas) tuning
2. Predicts noise (epsilon) instead of velocity
3. Stochastic sampling (SDE) vs deterministic (ODE)
"""

import torch
import torch.nn as nn
import numpy as np
import time
import json
from pathlib import Path
from datetime import datetime


class NoiseSchedule:
    """Linear beta schedule for DDPM."""
    
    def __init__(self, timesteps=1000, beta_start=1e-4, beta_end=0.02, device='cpu'):
        self.timesteps = timesteps
        self.device = device
        
        # Linear schedule
        self.betas = torch.linspace(beta_start, beta_end, timesteps, device=device)
        self.alphas = 1.0 - self.betas
        self.alphas_cumprod = torch.cumprod(self.alphas, dim=0)
        self.alphas_cumprod_prev = torch.cat([torch.tensor([1.0], device=device), self.alphas_cumprod[:-1]])
        
        # For q(x_t | x_0)
        self.sqrt_alphas_cumprod = torch.sqrt(self.alphas_cumprod)
        self.sqrt_one_minus_alphas_cumprod = torch.sqrt(1.0 - self.alphas_cumprod)
        
        # For posterior q(x_{t-1} | x_t, x_0)
        self.posterior_variance = self.betas * (1.0 - self.alphas_cumprod_prev) / (1.0 - self.alphas_cumprod)
        self.posterior_variance[0] = 0  # No variance at t=0
        
    def q_sample(self, x_0, t, noise=None):
        """Forward diffusion: q(x_t | x_0)"""
        if noise is None:
            noise = torch.randn_like(x_0)
        
        sqrt_alpha = self.sqrt_alphas_cumprod[t].view(-1, 1)
        sqrt_one_minus_alpha = self.sqrt_one_minus_alphas_cumprod[t].view(-1, 1)
        
        return sqrt_alpha * x_0 + sqrt_one_minus_alpha * noise


class NoiseNetwork(nn.Module):
    """
    MLP network for noise prediction.
    Architecture matches Flow Matching VelocityNetwork for fair comparison:
    Input: [x (2D), t (embedded)] -> hidden layers -> output (2D noise)
    """
    def __init__(self, input_dim=2, hidden_dim=128, num_layers=3):
        super().__init__()
        
        # Time embedding (sinusoidal, same as Flow Matching)
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
        Predict noise at position x and timestep t.
        
        Args:
            x: [batch, 2] position
            t: [batch] timestep (integer, 0 to T-1)
        Returns:
            noise: [batch, 2] predicted noise
        """
        # Normalize timestep to [0, 1] for embedding
        t_normalized = t.float() / 1000.0
        t_emb = self.time_embedding(t_normalized)
        inp = torch.cat([x, t_emb], dim=-1)
        return self.net(inp)


class DDPMTrainer:
    """
    DDPM trainer for 2D data.
    
    Loss: MSE between predicted noise and actual noise added.
    """
    
    def __init__(self, model, schedule, lr=1e-3, device='cpu'):
        self.model = model.to(device)
        self.schedule = schedule
        self.device = device
        self.optimizer = torch.optim.Adam(model.parameters(), lr=lr)
        
    def compute_loss(self, x0):
        """
        Compute DDPM loss.
        
        Args:
            x0: [batch, 2] data samples (clean data)
        Returns:
            loss: scalar MSE loss
        """
        batch_size = x0.shape[0]
        
        # Sample random timesteps
        t = torch.randint(0, self.schedule.timesteps, (batch_size,), device=self.device)
        
        # Sample noise
        noise = torch.randn_like(x0)
        
        # Add noise: x_t = sqrt(alpha_bar) * x_0 + sqrt(1 - alpha_bar) * noise
        x_t = self.schedule.q_sample(x0, t, noise)
        
        # Predict noise
        noise_pred = self.model(x_t, t)
        
        # MSE loss
        loss = ((noise_pred - noise) ** 2).mean()
        
        return loss
    
    def train_step(self, x0):
        """Single training step."""
        self.optimizer.zero_grad()
        loss = self.compute_loss(x0)
        loss.backward()
        self.optimizer.step()
        return loss.item()


class DDPMSampler:
    """
    Sampler for DDPM models using reverse diffusion.
    """
    
    def __init__(self, model, schedule, device='cpu'):
        self.model = model.to(device)
        self.schedule = schedule
        self.device = device
        
    @torch.no_grad()
    def sample(self, n_samples, n_steps=None):
        """
        Generate samples using DDPM reverse process.
        
        Args:
            n_samples: number of samples to generate
            n_steps: ignored (uses schedule.timesteps for fair comparison)
        Returns:
            samples: [n_samples, 2] generated samples
        """
        self.model.eval()
        
        # Start from pure noise
        x = torch.randn(n_samples, 2, device=self.device)
        
        # Reverse diffusion
        for t in reversed(range(self.schedule.timesteps)):
            t_batch = torch.full((n_samples,), t, device=self.device, dtype=torch.long)
            
            # Predict noise
            noise_pred = self.model(x, t_batch)
            
            # Compute mean
            alpha = self.schedule.alphas[t]
            alpha_cumprod = self.schedule.alphas_cumprod[t]
            beta = self.schedule.betas[t]
            
            # x_{t-1} mean: (1/sqrt(alpha)) * (x_t - beta/sqrt(1-alpha_bar) * noise)
            mean = (1 / torch.sqrt(alpha)) * (
                x - (beta / torch.sqrt(1 - alpha_cumprod)) * noise_pred
            )
            
            # Add noise (except at t=0)
            if t > 0:
                noise = torch.randn_like(x)
                variance = self.schedule.posterior_variance[t]
                x = mean + torch.sqrt(variance) * noise
            else:
                x = mean
        
        return x


def create_2d_gaussian_mixture(n_samples, n_modes=8, std=0.1):
    """
    Create 2D Gaussian mixture data (ring of Gaussians).
    Same as Flow Matching for fair comparison.
    """
    angles = np.linspace(0, 2 * np.pi, n_modes, endpoint=False)
    centers = np.stack([np.cos(angles), np.sin(angles)], axis=1) * 2.0
    
    mode_idx = np.random.randint(0, n_modes, n_samples)
    samples = centers[mode_idx] + np.random.randn(n_samples, 2) * std
    
    return torch.tensor(samples, dtype=torch.float32)


def benchmark_training(n_epochs=2000, batch_size=256, n_data=10000, timesteps=1000, device='cpu'):
    """
    Benchmark DDPM training.
    
    Returns:
        dict with training metrics
    """
    print(f"DDPM 2D Baseline Benchmark")
    print(f"Device: {device}")
    print(f"Epochs: {n_epochs}, Batch size: {batch_size}, Timesteps: {timesteps}")
    print("-" * 50)
    
    # Create schedule and model
    schedule = NoiseSchedule(timesteps=timesteps, device=device)
    model = NoiseNetwork(input_dim=2, hidden_dim=128, num_layers=3)
    trainer = DDPMTrainer(model, schedule, lr=1e-3, device=device)
    
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
    sampler = DDPMSampler(model, schedule, device=device)
    sample_start = time.time()
    samples = sampler.sample(1000)
    sample_time = time.time() - sample_start
    
    results = {
        "method": "ddpm",
        "n_epochs": n_epochs,
        "batch_size": batch_size,
        "timesteps": timesteps,
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
    with open(output_dir / "ddpm_metrics.json", "w") as f:
        json.dump(results, f, indent=2)
    
    # Save samples
    np.save(output_dir / "ddpm_samples.npy", samples)
    
    # Save loss curve
    np.save(output_dir / "ddpm_losses.npy", np.array(losses))
    
    print(f"Results saved to {output_dir}")


def main():
    """Run DDPM 2D baseline benchmark."""
    import argparse
    
    parser = argparse.ArgumentParser(description="DDPM 2D Baseline Benchmark")
    parser.add_argument("--epochs", type=int, default=2000, help="Training epochs")
    parser.add_argument("--batch-size", type=int, default=256, help="Batch size")
    parser.add_argument("--timesteps", type=int, default=1000, help="Diffusion timesteps")
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
        timesteps=args.timesteps,
        device=device
    )
    
    save_results(args.output, results, samples, losses)
    
    # Save model
    torch.save(model.state_dict(), Path(args.output) / "ddpm_model.pt")
    
    return results


if __name__ == "__main__":
    main()
