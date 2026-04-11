#!/usr/bin/env python3
"""
Minimal GAN for 2D Gaussian Mixture
Trains in <10 minutes on CPU, produces loss curves and sample grid.
"""

import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
import time

# Reproducibility
torch.manual_seed(42)
np.random.seed(42)

# ============== Data: 2D Gaussian Mixture ==============
def sample_gaussian_mixture(n_samples, n_modes=8, radius=2.0, std=0.05):
    """Generate samples from a ring of Gaussian modes."""
    angles = np.linspace(0, 2 * np.pi, n_modes, endpoint=False)
    centers = np.stack([radius * np.cos(angles), radius * np.sin(angles)], axis=1)
    
    # Randomly pick modes and add noise
    mode_indices = np.random.randint(0, n_modes, n_samples)
    samples = centers[mode_indices] + std * np.random.randn(n_samples, 2)
    return torch.tensor(samples, dtype=torch.float32)

# ============== Generator ==============
class Generator(nn.Module):
    def __init__(self, latent_dim=2, hidden_dim=128, output_dim=2):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(latent_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, output_dim),
        )
    
    def forward(self, z):
        return self.net(z)

# ============== Discriminator ==============
class Discriminator(nn.Module):
    def __init__(self, input_dim=2, hidden_dim=128):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.LeakyReLU(0.2),
            nn.Linear(hidden_dim, hidden_dim),
            nn.LeakyReLU(0.2),
            nn.Linear(hidden_dim, 1),
            nn.Sigmoid(),
        )
    
    def forward(self, x):
        return self.net(x)

# ============== Training ==============
def train_gan(
    n_epochs=2000,
    batch_size=256,
    latent_dim=2,
    lr_g=1e-4,
    lr_d=1e-4,
    log_interval=100,
):
    device = torch.device('cpu')
    print(f"Training on: {device}")
    
    # Models
    G = Generator(latent_dim=latent_dim).to(device)
    D = Discriminator().to(device)
    
    # Optimizers
    opt_G = optim.Adam(G.parameters(), lr=lr_g, betas=(0.5, 0.999))
    opt_D = optim.Adam(D.parameters(), lr=lr_d, betas=(0.5, 0.999))
    
    criterion = nn.BCELoss()
    
    # Tracking
    d_losses, g_losses = [], []
    mode_coverage_log = []
    
    start_time = time.time()
    
    for epoch in range(n_epochs):
        # Sample real data
        real_data = sample_gaussian_mixture(batch_size).to(device)
        
        # Labels
        real_labels = torch.ones(batch_size, 1).to(device)
        fake_labels = torch.zeros(batch_size, 1).to(device)
        
        # ===== Train Discriminator =====
        z = torch.randn(batch_size, latent_dim).to(device)
        fake_data = G(z).detach()
        
        D.zero_grad()
        d_real = D(real_data)
        d_fake = D(fake_data)
        
        loss_d_real = criterion(d_real, real_labels)
        loss_d_fake = criterion(d_fake, fake_labels)
        loss_d = loss_d_real + loss_d_fake
        
        loss_d.backward()
        opt_D.step()
        
        # ===== Train Generator =====
        z = torch.randn(batch_size, latent_dim).to(device)
        fake_data = G(z)
        
        G.zero_grad()
        d_fake = D(fake_data)
        loss_g = criterion(d_fake, real_labels)  # G wants D to think fake is real
        
        loss_g.backward()
        opt_G.step()
        
        d_losses.append(loss_d.item())
        g_losses.append(loss_g.item())
        
        # Mode coverage check
        if (epoch + 1) % log_interval == 0:
            coverage = check_mode_coverage(G, latent_dim, device)
            mode_coverage_log.append((epoch + 1, coverage))
            elapsed = time.time() - start_time
            print(f"Epoch {epoch+1}/{n_epochs} | D Loss: {loss_d.item():.4f} | G Loss: {loss_g.item():.4f} | Modes: {coverage}/8 | Time: {elapsed:.1f}s")
    
    total_time = time.time() - start_time
    print(f"\nTraining completed in {total_time:.1f} seconds")
    
    return G, D, d_losses, g_losses, mode_coverage_log

def check_mode_coverage(G, latent_dim, device, n_samples=1000, threshold=0.3):
    """Count how many of the 8 modes the generator covers."""
    G.eval()
    with torch.no_grad():
        z = torch.randn(n_samples, latent_dim).to(device)
        samples = G(z).cpu().numpy()
    G.train()
    
    # Mode centers
    n_modes = 8
    radius = 2.0
    angles = np.linspace(0, 2 * np.pi, n_modes, endpoint=False)
    centers = np.stack([radius * np.cos(angles), radius * np.sin(angles)], axis=1)
    
    # Check which modes have nearby samples
    covered = 0
    for center in centers:
        distances = np.linalg.norm(samples - center, axis=1)
        if np.any(distances < threshold):
            covered += 1
    
    return covered

# ============== Visualization ==============
def plot_results(G, d_losses, g_losses, mode_coverage_log, latent_dim, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)
    
    # --- Loss curves ---
    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    
    axes[0].plot(d_losses, label='Discriminator', alpha=0.7)
    axes[0].plot(g_losses, label='Generator', alpha=0.7)
    axes[0].set_xlabel('Epoch')
    axes[0].set_ylabel('Loss')
    axes[0].set_title('GAN Training Losses')
    axes[0].legend()
    axes[0].grid(True, alpha=0.3)
    
    # Mode coverage over time
    epochs, coverages = zip(*mode_coverage_log)
    axes[1].plot(epochs, coverages, 'o-', color='green')
    axes[1].set_xlabel('Epoch')
    axes[1].set_ylabel('Modes Covered (out of 8)')
    axes[1].set_title('Mode Coverage Over Training')
    axes[1].set_ylim(0, 9)
    axes[1].grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(output_dir / 'loss_curves.png', dpi=150)
    plt.close()
    print(f"Saved: {output_dir / 'loss_curves.png'}")
    
    # --- Sample grid ---
    G.eval()
    with torch.no_grad():
        z = torch.randn(1000, latent_dim)
        fake_samples = G(z).numpy()
    
    real_samples = sample_gaussian_mixture(1000).numpy()
    
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    
    axes[0].scatter(real_samples[:, 0], real_samples[:, 1], alpha=0.5, s=10, c='blue')
    axes[0].set_title('Real Data (8-mode Gaussian Mixture)')
    axes[0].set_xlim(-3.5, 3.5)
    axes[0].set_ylim(-3.5, 3.5)
    axes[0].set_aspect('equal')
    axes[0].grid(True, alpha=0.3)
    
    axes[1].scatter(fake_samples[:, 0], fake_samples[:, 1], alpha=0.5, s=10, c='red')
    axes[1].set_title('Generated Samples')
    axes[1].set_xlim(-3.5, 3.5)
    axes[1].set_ylim(-3.5, 3.5)
    axes[1].set_aspect('equal')
    axes[1].grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(output_dir / 'sample_grid.png', dpi=150)
    plt.close()
    print(f"Saved: {output_dir / 'sample_grid.png'}")

def analyze_training(d_losses, g_losses, mode_coverage_log):
    """Analyze training for mode collapse and instability."""
    report = []
    report.append("=" * 50)
    report.append("TRAINING ANALYSIS")
    report.append("=" * 50)
    
    # Check for mode collapse
    final_coverage = mode_coverage_log[-1][1] if mode_coverage_log else 0
    if final_coverage < 8:
        report.append(f"⚠️  MODE COLLAPSE DETECTED: Only {final_coverage}/8 modes covered")
    else:
        report.append(f"✓  Full mode coverage: {final_coverage}/8 modes")
    
    # Check for training instability (high variance in recent losses)
    recent_d = d_losses[-200:] if len(d_losses) >= 200 else d_losses
    recent_g = g_losses[-200:] if len(g_losses) >= 200 else g_losses
    
    d_std = np.std(recent_d)
    g_std = np.std(recent_g)
    
    if d_std > 0.5 or g_std > 0.5:
        report.append(f"⚠️  TRAINING INSTABILITY: D_std={d_std:.3f}, G_std={g_std:.3f}")
    else:
        report.append(f"✓  Training stable: D_std={d_std:.3f}, G_std={g_std:.3f}")
    
    # Check for D winning (G loss very high)
    if np.mean(recent_g) > 5.0:
        report.append("⚠️  DISCRIMINATOR DOMINATING: Generator struggling to learn")
    
    # Check for G winning (D loss very high)
    if np.mean(recent_d) > 3.0:
        report.append("⚠️  GENERATOR DOMINATING: Discriminator can't distinguish")
    
    report.append("=" * 50)
    return "\n".join(report)

# ============== Main ==============
if __name__ == "__main__":
    output_dir = Path(__file__).parent
    
    print("Starting GAN training on 2D Gaussian Mixture...")
    print("Target: 8 modes arranged in a ring")
    print("-" * 50)
    
    G, D, d_losses, g_losses, mode_coverage_log = train_gan(
        n_epochs=2000,
        batch_size=256,
        latent_dim=2,
        lr_g=1e-4,
        lr_d=1e-4,
        log_interval=100,
    )
    
    # Analysis
    analysis = analyze_training(d_losses, g_losses, mode_coverage_log)
    print(analysis)
    
    # Save analysis
    with open(output_dir / 'training_report.txt', 'w') as f:
        f.write(analysis)
    print(f"Saved: {output_dir / 'training_report.txt'}")
    
    # Visualizations
    plot_results(G, d_losses, g_losses, mode_coverage_log, latent_dim=2, output_dir=output_dir)
    
    print("\nDone! Check output files in:", output_dir)
