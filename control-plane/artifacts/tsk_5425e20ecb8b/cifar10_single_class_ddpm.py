#!/usr/bin/env python3
"""Minimal DDPM for single-class CIFAR-10 (32x32 RGB)."""

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Subset
from torchvision import datasets, transforms
from torchvision.utils import save_image
import numpy as np
import time
import os

# Config
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
TIMESTEPS = 100  # As specified
EPOCHS = 12  # Middle of 10-15 range
BATCH_SIZE = 128
LR = 2e-4
IMG_SIZE = 32
CHANNELS = 3

# CIFAR-10 class to train on (1 = automobile - simple rectangular structures)
TARGET_CLASS = 1  # automobile
CLASS_NAMES = ['airplane', 'automobile', 'bird', 'cat', 'deer', 'dog', 'frog', 'horse', 'ship', 'truck']

print(f"Training diffusion model on: {DEVICE}")
print(f"Target class: {CLASS_NAMES[TARGET_CLASS]} (class {TARGET_CLASS})")

# Beta schedule (linear)
beta = torch.linspace(1e-4, 0.02, TIMESTEPS, device=DEVICE)
alpha = 1 - beta
alpha_bar = torch.cumprod(alpha, dim=0)

def forward_diffusion(x0, t, noise=None):
    """Add noise to images according to diffusion schedule."""
    if noise is None:
        noise = torch.randn_like(x0)
    sqrt_alpha_bar = alpha_bar[t].sqrt().view(-1, 1, 1, 1)
    sqrt_one_minus = (1 - alpha_bar[t]).sqrt().view(-1, 1, 1, 1)
    return sqrt_alpha_bar * x0 + sqrt_one_minus * noise, noise

class SinusoidalPosEmb(nn.Module):
    """Sinusoidal positional embeddings for timesteps."""
    def __init__(self, dim):
        super().__init__()
        self.dim = dim
    
    def forward(self, t):
        half_dim = self.dim // 2
        emb = np.log(10000) / (half_dim - 1)
        emb = torch.exp(torch.arange(half_dim, device=t.device) * -emb)
        emb = t[:, None] * emb[None, :]
        return torch.cat([torch.sin(emb), torch.cos(emb)], dim=-1)

class ResBlock(nn.Module):
    """Residual block with time conditioning."""
    def __init__(self, in_ch, out_ch, time_dim):
        super().__init__()
        self.conv1 = nn.Conv2d(in_ch, out_ch, 3, padding=1)
        self.conv2 = nn.Conv2d(out_ch, out_ch, 3, padding=1)
        self.time_mlp = nn.Linear(time_dim, out_ch)
        self.norm1 = nn.GroupNorm(8, out_ch)
        self.norm2 = nn.GroupNorm(8, out_ch)
        self.skip = nn.Conv2d(in_ch, out_ch, 1) if in_ch != out_ch else nn.Identity()
    
    def forward(self, x, t_emb):
        h = self.norm1(F.silu(self.conv1(x)))
        h = h + self.time_mlp(t_emb)[:, :, None, None]
        h = self.norm2(F.silu(self.conv2(h)))
        return h + self.skip(x)

class SmallUNet(nn.Module):
    """Small UNet for 32x32 RGB images with time conditioning."""
    def __init__(self, ch=64, time_dim=128):
        super().__init__()
        self.time_dim = time_dim
        
        # Time embedding
        self.time_mlp = nn.Sequential(
            SinusoidalPosEmb(time_dim),
            nn.Linear(time_dim, time_dim * 2),
            nn.GELU(),
            nn.Linear(time_dim * 2, time_dim)
        )
        
        # Encoder
        self.conv_in = nn.Conv2d(CHANNELS, ch, 3, padding=1)
        
        self.down1 = ResBlock(ch, ch, time_dim)
        self.down1_pool = nn.Conv2d(ch, ch, 4, stride=2, padding=1)  # 32->16
        
        self.down2 = ResBlock(ch, ch * 2, time_dim)
        self.down2_pool = nn.Conv2d(ch * 2, ch * 2, 4, stride=2, padding=1)  # 16->8
        
        self.down3 = ResBlock(ch * 2, ch * 4, time_dim)
        self.down3_pool = nn.Conv2d(ch * 4, ch * 4, 4, stride=2, padding=1)  # 8->4
        
        # Middle
        self.mid1 = ResBlock(ch * 4, ch * 4, time_dim)
        self.mid2 = ResBlock(ch * 4, ch * 4, time_dim)
        
        # Decoder
        self.up3 = nn.ConvTranspose2d(ch * 4, ch * 4, 4, stride=2, padding=1)  # 4->8
        self.up3_res = ResBlock(ch * 8, ch * 2, time_dim)  # concat skip
        
        self.up2 = nn.ConvTranspose2d(ch * 2, ch * 2, 4, stride=2, padding=1)  # 8->16
        self.up2_res = ResBlock(ch * 4, ch, time_dim)  # concat skip
        
        self.up1 = nn.ConvTranspose2d(ch, ch, 4, stride=2, padding=1)  # 16->32
        self.up1_res = ResBlock(ch * 2, ch, time_dim)  # concat skip
        
        # Output
        self.conv_out = nn.Sequential(
            nn.GroupNorm(8, ch),
            nn.SiLU(),
            nn.Conv2d(ch, CHANNELS, 3, padding=1)
        )
        
    def forward(self, x, t):
        # Time embedding
        t_emb = self.time_mlp(t.float())
        
        # Encoder
        h = self.conv_in(x)
        
        h1 = self.down1(h, t_emb)
        h = self.down1_pool(h1)
        
        h2 = self.down2(h, t_emb)
        h = self.down2_pool(h2)
        
        h3 = self.down3(h, t_emb)
        h = self.down3_pool(h3)
        
        # Middle
        h = self.mid1(h, t_emb)
        h = self.mid2(h, t_emb)
        
        # Decoder with skip connections
        h = self.up3(h)
        h = torch.cat([h, h3], dim=1)
        h = self.up3_res(h, t_emb)
        
        h = self.up2(h)
        h = torch.cat([h, h2], dim=1)
        h = self.up2_res(h, t_emb)
        
        h = self.up1(h)
        h = torch.cat([h, h1], dim=1)
        h = self.up1_res(h, t_emb)
        
        return self.conv_out(h)

@torch.no_grad()
def sample(model, n_samples=64):
    """Generate samples using DDPM sampling."""
    model.eval()
    x = torch.randn(n_samples, CHANNELS, IMG_SIZE, IMG_SIZE, device=DEVICE)
    
    for t in reversed(range(TIMESTEPS)):
        t_batch = torch.full((n_samples,), t, device=DEVICE, dtype=torch.long)
        
        # Predict noise
        pred_noise = model(x, t_batch)
        
        # DDPM update
        alpha_t = alpha[t]
        alpha_bar_t = alpha_bar[t]
        
        # Mean
        coef1 = 1 / alpha_t.sqrt()
        coef2 = (1 - alpha_t) / (1 - alpha_bar_t).sqrt()
        mean = coef1 * (x - coef2 * pred_noise)
        
        # Add noise (except at t=0)
        if t > 0:
            noise = torch.randn_like(x)
            sigma = beta[t].sqrt()
            x = mean + sigma * noise
        else:
            x = mean
    
    return x.clamp(-1, 1)

def train():
    print("Loading CIFAR-10...")
    transform = transforms.Compose([
        transforms.RandomHorizontalFlip(),  # Data augmentation
        transforms.ToTensor(),
        transforms.Normalize([0.5, 0.5, 0.5], [0.5, 0.5, 0.5])  # Scale to [-1, 1]
    ])
    
    # Load full dataset
    full_dataset = datasets.CIFAR10('./data', train=True, download=True, transform=transform)
    
    # Filter to single class
    indices = [i for i, (_, label) in enumerate(full_dataset) if label == TARGET_CLASS]
    dataset = Subset(full_dataset, indices)
    
    print(f"Filtered to class '{CLASS_NAMES[TARGET_CLASS]}': {len(dataset)} images")
    
    loader = DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=True, num_workers=4, pin_memory=True, drop_last=True)
    
    print(f"Batches per epoch: {len(loader)}")
    
    model = SmallUNet(ch=64).to(DEVICE)
    optimizer = torch.optim.AdamW(model.parameters(), lr=LR)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=EPOCHS)
    
    param_count = sum(p.numel() for p in model.parameters())
    print(f"Model parameters: {param_count:,}")
    
    # Output directory
    output_dir = "/home/ubuntu/.openclaw/workspace/control-plane/artifacts/tsk_5425e20ecb8b"
    os.makedirs(output_dir, exist_ok=True)
    
    start_time = time.time()
    
    for epoch in range(EPOCHS):
        model.train()
        total_loss = 0
        
        for batch_idx, (x, _) in enumerate(loader):
            x = x.to(DEVICE)
            
            # Sample random timesteps
            t = torch.randint(0, TIMESTEPS, (x.size(0),), device=DEVICE)
            
            # Add noise
            x_noisy, noise = forward_diffusion(x, t)
            
            # Predict noise
            pred_noise = model(x_noisy, t)
            
            # MSE loss
            loss = F.mse_loss(pred_noise, noise)
            
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            
            total_loss += loss.item()
        
        scheduler.step()
        avg_loss = total_loss / len(loader)
        elapsed = time.time() - start_time
        print(f"Epoch {epoch+1}/{EPOCHS} | Loss: {avg_loss:.4f} | Time: {elapsed:.1f}s | LR: {scheduler.get_last_lr()[0]:.2e}")
        
        # Generate intermediate samples every 4 epochs
        if (epoch + 1) % 4 == 0:
            samples = sample(model, n_samples=16)
            samples = (samples + 1) / 2
            save_image(samples, f"{output_dir}/samples_epoch_{epoch+1}.png", nrow=4, padding=2)
            print(f"  Saved intermediate samples at epoch {epoch+1}")
    
    total_time = time.time() - start_time
    print(f"\nTraining complete in {total_time:.1f}s ({total_time/60:.1f} min)")
    
    # Generate final samples
    print("Generating final samples...")
    samples = sample(model, n_samples=64)
    
    # Save final grid
    output_path = os.path.join(output_dir, f"cifar10_{CLASS_NAMES[TARGET_CLASS]}_ddpm_final.png")
    samples_vis = (samples + 1) / 2
    save_image(samples_vis, output_path, nrow=8, padding=2)
    
    # Also save real samples for comparison
    real_loader = DataLoader(dataset, batch_size=64, shuffle=True)
    real_samples, _ = next(iter(real_loader))
    real_samples = (real_samples + 1) / 2
    real_path = os.path.join(output_dir, f"cifar10_{CLASS_NAMES[TARGET_CLASS]}_real.png")
    save_image(real_samples, real_path, nrow=8, padding=2)
    
    print(f"\n{'='*60}")
    print(f"RESULTS - CIFAR-10 Single-Class Diffusion ({CLASS_NAMES[TARGET_CLASS]})")
    print(f"{'='*60}")
    print(f"  Training time: {total_time:.1f}s ({total_time/60:.1f} min)")
    print(f"  Final loss: {avg_loss:.4f}")
    print(f"  Model parameters: {param_count:,}")
    print(f"  Generated samples: {output_path}")
    print(f"  Real samples (comparison): {real_path}")
    print(f"{'='*60}")
    
    return output_path, real_path, total_time, avg_loss

if __name__ == "__main__":
    output_path, real_path, train_time, final_loss = train()
