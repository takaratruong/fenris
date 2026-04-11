#!/usr/bin/env python3
"""
Minimal VAE prototype for 8x8 downsampled MNIST.
Target: Train in <10 minutes on CPU.
Outputs: loss curve, reconstruction grid, latent interpolation PNGs.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
from torchvision import datasets, transforms
import matplotlib.pyplot as plt
import numpy as np
import time
import os

# Ensure reproducibility
torch.manual_seed(42)
np.random.seed(42)

# Output directory
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# Hyperparameters
LATENT_DIM = 2  # 2D latent space for visualization
INPUT_DIM = 8 * 8  # 8x8 downsampled MNIST
HIDDEN_DIM = 64
BATCH_SIZE = 128
EPOCHS = 30
LR = 1e-3


class VAE(nn.Module):
    """Minimal VAE with 2D latent space."""
    
    def __init__(self, input_dim=INPUT_DIM, hidden_dim=HIDDEN_DIM, latent_dim=LATENT_DIM):
        super().__init__()
        self.latent_dim = latent_dim
        
        # Encoder
        self.encoder = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
        )
        self.fc_mu = nn.Linear(hidden_dim, latent_dim)
        self.fc_logvar = nn.Linear(hidden_dim, latent_dim)
        
        # Decoder
        self.decoder = nn.Sequential(
            nn.Linear(latent_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, input_dim),
            nn.Sigmoid(),
        )
    
    def encode(self, x):
        h = self.encoder(x)
        return self.fc_mu(h), self.fc_logvar(h)
    
    def reparameterize(self, mu, logvar):
        std = torch.exp(0.5 * logvar)
        eps = torch.randn_like(std)
        return mu + eps * std
    
    def decode(self, z):
        return self.decoder(z)
    
    def forward(self, x):
        mu, logvar = self.encode(x)
        z = self.reparameterize(mu, logvar)
        return self.decode(z), mu, logvar


def vae_loss(recon_x, x, mu, logvar):
    """VAE loss = reconstruction + KL divergence."""
    # BCE reconstruction loss
    recon_loss = F.binary_cross_entropy(recon_x, x, reduction='sum')
    # KL divergence: -0.5 * sum(1 + log(sigma^2) - mu^2 - sigma^2)
    kl_loss = -0.5 * torch.sum(1 + logvar - mu.pow(2) - logvar.exp())
    return recon_loss + kl_loss


def get_dataloader(train=True):
    """Load 8x8 downsampled MNIST."""
    transform = transforms.Compose([
        transforms.Resize((8, 8)),
        transforms.ToTensor(),
    ])
    
    data_path = os.path.join(os.path.dirname(OUT_DIR), 'data')
    dataset = datasets.MNIST(
        root=data_path,
        train=train,
        download=True,
        transform=transform
    )
    return DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=train)


def train_epoch(model, loader, optimizer, device):
    model.train()
    total_loss = 0
    for batch_idx, (data, _) in enumerate(loader):
        data = data.view(-1, INPUT_DIM).to(device)
        optimizer.zero_grad()
        recon, mu, logvar = model(data)
        loss = vae_loss(recon, data, mu, logvar)
        loss.backward()
        optimizer.step()
        total_loss += loss.item()
    return total_loss / len(loader.dataset)


def evaluate(model, loader, device):
    model.eval()
    total_loss = 0
    with torch.no_grad():
        for data, _ in loader:
            data = data.view(-1, INPUT_DIM).to(device)
            recon, mu, logvar = model(data)
            total_loss += vae_loss(recon, data, mu, logvar).item()
    return total_loss / len(loader.dataset)


def plot_loss_curve(train_losses, test_losses):
    """Save loss curve plot."""
    plt.figure(figsize=(10, 6))
    plt.plot(train_losses, label='Train Loss', linewidth=2)
    plt.plot(test_losses, label='Test Loss', linewidth=2)
    plt.xlabel('Epoch', fontsize=12)
    plt.ylabel('Loss (per sample)', fontsize=12)
    plt.title('VAE Training Loss Curve', fontsize=14)
    plt.legend(fontsize=11)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(OUT_DIR, 'loss_curve.png'), dpi=150)
    plt.close()
    print(f"Saved: loss_curve.png")


def plot_reconstructions(model, loader, device, n_samples=10):
    """Save reconstruction comparison grid."""
    model.eval()
    data, _ = next(iter(loader))
    data = data[:n_samples].view(-1, INPUT_DIM).to(device)
    
    with torch.no_grad():
        recon, _, _ = model(data)
    
    fig, axes = plt.subplots(2, n_samples, figsize=(n_samples * 1.5, 3))
    for i in range(n_samples):
        # Original
        axes[0, i].imshow(data[i].cpu().view(8, 8), cmap='gray')
        axes[0, i].axis('off')
        if i == 0:
            axes[0, i].set_title('Original', fontsize=10)
        
        # Reconstruction
        axes[1, i].imshow(recon[i].cpu().view(8, 8), cmap='gray')
        axes[1, i].axis('off')
        if i == 0:
            axes[1, i].set_title('Recon', fontsize=10)
    
    plt.suptitle('VAE Reconstructions (8x8 MNIST)', fontsize=12)
    plt.tight_layout()
    plt.savefig(os.path.join(OUT_DIR, 'reconstructions.png'), dpi=150)
    plt.close()
    print(f"Saved: reconstructions.png")


def plot_latent_interpolation(model, device, n_steps=10):
    """Generate latent space interpolation grid."""
    model.eval()
    
    # Create grid in latent space
    z_range = torch.linspace(-3, 3, n_steps)
    grid_size = n_steps
    
    fig, axes = plt.subplots(grid_size, grid_size, figsize=(10, 10))
    
    with torch.no_grad():
        for i, z1 in enumerate(z_range):
            for j, z2 in enumerate(z_range):
                z = torch.tensor([[z1, z2]], dtype=torch.float32).to(device)
                sample = model.decode(z)
                axes[i, j].imshow(sample.cpu().view(8, 8), cmap='gray')
                axes[i, j].axis('off')
    
    plt.suptitle('VAE Latent Space Interpolation (z1 vs z2)', fontsize=14)
    plt.tight_layout()
    plt.savefig(os.path.join(OUT_DIR, 'latent_interpolation.png'), dpi=150)
    plt.close()
    print(f"Saved: latent_interpolation.png")


def plot_latent_space(model, loader, device):
    """Visualize latent space colored by digit class."""
    model.eval()
    z_all = []
    labels_all = []
    
    with torch.no_grad():
        for data, labels in loader:
            data = data.view(-1, INPUT_DIM).to(device)
            mu, _ = model.encode(data)
            z_all.append(mu.cpu().numpy())
            labels_all.append(labels.numpy())
    
    z_all = np.concatenate(z_all, axis=0)
    labels_all = np.concatenate(labels_all, axis=0)
    
    plt.figure(figsize=(10, 8))
    scatter = plt.scatter(z_all[:, 0], z_all[:, 1], c=labels_all, cmap='tab10', 
                         alpha=0.6, s=5)
    plt.colorbar(scatter, label='Digit')
    plt.xlabel('z1', fontsize=12)
    plt.ylabel('z2', fontsize=12)
    plt.title('VAE Latent Space (Test Set)', fontsize=14)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(OUT_DIR, 'latent_space.png'), dpi=150)
    plt.close()
    print(f"Saved: latent_space.png")


def main():
    print("=" * 60)
    print("VAE Prototype Training")
    print("=" * 60)
    
    device = torch.device('cpu')
    print(f"Device: {device}")
    print(f"Latent dim: {LATENT_DIM}, Hidden dim: {HIDDEN_DIM}")
    print(f"Batch size: {BATCH_SIZE}, Epochs: {EPOCHS}, LR: {LR}")
    print()
    
    # Load data
    print("Loading 8x8 MNIST...")
    train_loader = get_dataloader(train=True)
    test_loader = get_dataloader(train=False)
    print(f"Train samples: {len(train_loader.dataset)}")
    print(f"Test samples: {len(test_loader.dataset)}")
    print()
    
    # Initialize model
    model = VAE().to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=LR)
    
    param_count = sum(p.numel() for p in model.parameters())
    print(f"Model parameters: {param_count:,}")
    print()
    
    # Training loop
    train_losses = []
    test_losses = []
    
    start_time = time.time()
    print("Training...")
    
    for epoch in range(1, EPOCHS + 1):
        epoch_start = time.time()
        train_loss = train_epoch(model, train_loader, optimizer, device)
        test_loss = evaluate(model, test_loader, device)
        epoch_time = time.time() - epoch_start
        
        train_losses.append(train_loss)
        test_losses.append(test_loss)
        
        if epoch % 5 == 0 or epoch == 1:
            print(f"Epoch {epoch:3d}/{EPOCHS} | Train: {train_loss:.2f} | "
                  f"Test: {test_loss:.2f} | Time: {epoch_time:.1f}s")
    
    total_time = time.time() - start_time
    print()
    print(f"Training completed in {total_time:.1f}s ({total_time/60:.2f} min)")
    print(f"Final test loss: {test_losses[-1]:.2f}")
    print()
    
    # Generate outputs
    print("Generating visualizations...")
    plot_loss_curve(train_losses, test_losses)
    plot_reconstructions(model, test_loader, device)
    plot_latent_interpolation(model, device)
    plot_latent_space(model, test_loader, device)
    
    # Save model
    model_path = os.path.join(OUT_DIR, 'vae_model.pt')
    torch.save({
        'model_state_dict': model.state_dict(),
        'train_losses': train_losses,
        'test_losses': test_losses,
        'config': {
            'latent_dim': LATENT_DIM,
            'hidden_dim': HIDDEN_DIM,
            'input_dim': INPUT_DIM,
        }
    }, model_path)
    print(f"Saved: vae_model.pt")
    
    print()
    print("=" * 60)
    print("All outputs saved to:", OUT_DIR)
    print("=" * 60)
    
    return {
        'train_time_seconds': total_time,
        'final_train_loss': train_losses[-1],
        'final_test_loss': test_losses[-1],
        'param_count': param_count,
    }


if __name__ == '__main__':
    results = main()
    print("\nSummary:", results)
