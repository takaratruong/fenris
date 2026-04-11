#!/usr/bin/env python3
"""
Minimal Unconditional DDPM on MNIST
Target: visible denoising samples in <10 minutes wall time
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
from torchvision import datasets, transforms
from torchvision.utils import save_image, make_grid
import math
import os
from pathlib import Path

# Config
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
TIMESTEPS = 1000
BETA_START = 1e-4
BETA_END = 0.02
IMG_SIZE = 28
CHANNELS = 1
BATCH_SIZE = 128
LR = 2e-4
SAMPLE_EVERY = 500
MAX_STEPS = 5000
BASE_CHANNELS = 64

print(f"Using device: {DEVICE}")

# Output directory
OUT_DIR = Path(__file__).parent / "samples"
OUT_DIR.mkdir(exist_ok=True)

# ============ Noise Schedule ============
betas = torch.linspace(BETA_START, BETA_END, TIMESTEPS, device=DEVICE)
alphas = 1.0 - betas
alphas_cumprod = torch.cumprod(alphas, dim=0)
alphas_cumprod_prev = F.pad(alphas_cumprod[:-1], (1, 0), value=1.0)
sqrt_alphas_cumprod = torch.sqrt(alphas_cumprod)
sqrt_one_minus_alphas_cumprod = torch.sqrt(1.0 - alphas_cumprod)
sqrt_recip_alphas = torch.sqrt(1.0 / alphas)
posterior_variance = betas * (1.0 - alphas_cumprod_prev) / (1.0 - alphas_cumprod)

def q_sample(x0, t, noise=None):
    """Forward diffusion: add noise to x0 at timestep t"""
    if noise is None:
        noise = torch.randn_like(x0)
    sqrt_alpha = sqrt_alphas_cumprod[t][:, None, None, None]
    sqrt_one_minus_alpha = sqrt_one_minus_alphas_cumprod[t][:, None, None, None]
    return sqrt_alpha * x0 + sqrt_one_minus_alpha * noise

# ============ Simple UNet ============
class SinusoidalPosEmb(nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.dim = dim

    def forward(self, t):
        half_dim = self.dim // 2
        emb = math.log(10000) / (half_dim - 1)
        emb = torch.exp(torch.arange(half_dim, device=t.device) * -emb)
        emb = t[:, None] * emb[None, :]
        return torch.cat([emb.sin(), emb.cos()], dim=-1)

class ResBlock(nn.Module):
    def __init__(self, in_ch, out_ch, time_emb_dim):
        super().__init__()
        self.conv1 = nn.Conv2d(in_ch, out_ch, 3, padding=1)
        self.conv2 = nn.Conv2d(out_ch, out_ch, 3, padding=1)
        self.time_mlp = nn.Linear(time_emb_dim, out_ch)
        self.norm1 = nn.GroupNorm(8, out_ch)
        self.norm2 = nn.GroupNorm(8, out_ch)
        self.shortcut = nn.Conv2d(in_ch, out_ch, 1) if in_ch != out_ch else nn.Identity()

    def forward(self, x, t_emb):
        h = self.norm1(F.silu(self.conv1(x)))
        h = h + self.time_mlp(t_emb)[:, :, None, None]
        h = self.norm2(F.silu(self.conv2(h)))
        return h + self.shortcut(x)

class SimpleUNet(nn.Module):
    def __init__(self, in_ch=1, base_ch=64, time_emb_dim=256):
        super().__init__()
        self.time_mlp = nn.Sequential(
            SinusoidalPosEmb(time_emb_dim),
            nn.Linear(time_emb_dim, time_emb_dim),
            nn.SiLU(),
        )
        
        # Encoder
        self.enc1 = ResBlock(in_ch, base_ch, time_emb_dim)
        self.enc2 = ResBlock(base_ch, base_ch * 2, time_emb_dim)
        self.enc3 = ResBlock(base_ch * 2, base_ch * 4, time_emb_dim)
        
        self.down1 = nn.Conv2d(base_ch, base_ch, 4, 2, 1)
        self.down2 = nn.Conv2d(base_ch * 2, base_ch * 2, 4, 2, 1)
        
        # Middle
        self.mid = ResBlock(base_ch * 4, base_ch * 4, time_emb_dim)
        
        # Decoder
        self.up2 = nn.ConvTranspose2d(base_ch * 4, base_ch * 2, 4, 2, 1)
        self.up1 = nn.ConvTranspose2d(base_ch * 2, base_ch, 4, 2, 1)
        
        self.dec3 = ResBlock(base_ch * 4, base_ch * 2, time_emb_dim)
        self.dec2 = ResBlock(base_ch * 2, base_ch, time_emb_dim)
        self.dec1 = ResBlock(base_ch, base_ch, time_emb_dim)
        
        self.out = nn.Conv2d(base_ch, in_ch, 1)

    def forward(self, x, t):
        t_emb = self.time_mlp(t.float())
        
        # Encoder
        e1 = self.enc1(x, t_emb)        # 28x28
        e2 = self.enc2(self.down1(e1), t_emb)  # 14x14
        e3 = self.enc3(self.down2(e2), t_emb)  # 7x7
        
        # Middle
        m = self.mid(e3, t_emb)
        
        # Decoder with skip connections
        d3 = self.dec3(torch.cat([self.up2(m), e2], dim=1), t_emb)
        d2 = self.dec2(torch.cat([self.up1(d3), e1], dim=1), t_emb)
        d1 = self.dec1(d2, t_emb)
        
        return self.out(d1)

# ============ Sampling ============
@torch.no_grad()
def p_sample(model, x, t, t_index):
    """Single step of reverse diffusion"""
    beta_t = betas[t][:, None, None, None]
    sqrt_one_minus_alpha_t = sqrt_one_minus_alphas_cumprod[t][:, None, None, None]
    sqrt_recip_alpha_t = sqrt_recip_alphas[t][:, None, None, None]
    
    # Predict noise
    pred_noise = model(x, t)
    
    # Compute mean
    mean = sqrt_recip_alpha_t * (x - beta_t * pred_noise / sqrt_one_minus_alpha_t)
    
    if t_index == 0:
        return mean
    else:
        noise = torch.randn_like(x)
        var = torch.sqrt(posterior_variance[t][:, None, None, None])
        return mean + var * noise

@torch.no_grad()
def sample(model, n_samples=64):
    """Generate samples via reverse diffusion"""
    model.eval()
    x = torch.randn(n_samples, CHANNELS, IMG_SIZE, IMG_SIZE, device=DEVICE)
    
    for i in reversed(range(TIMESTEPS)):
        t = torch.full((n_samples,), i, device=DEVICE, dtype=torch.long)
        x = p_sample(model, x, t, i)
    
    model.train()
    return x

# ============ Training ============
def train():
    # Dataset
    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize([0.5], [0.5])  # Scale to [-1, 1]
    ])
    dataset = datasets.MNIST(root="./data", train=True, download=True, transform=transform)
    dataloader = DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=True, num_workers=2, pin_memory=True)
    
    # Model
    model = SimpleUNet(in_ch=CHANNELS, base_ch=BASE_CHANNELS).to(DEVICE)
    optimizer = torch.optim.Adam(model.parameters(), lr=LR)
    
    param_count = sum(p.numel() for p in model.parameters())
    print(f"Model parameters: {param_count:,}")
    
    # Training loop
    step = 0
    model.train()
    
    while step < MAX_STEPS:
        for batch, _ in dataloader:
            if step >= MAX_STEPS:
                break
                
            batch = batch.to(DEVICE)
            
            # Sample random timesteps
            t = torch.randint(0, TIMESTEPS, (batch.shape[0],), device=DEVICE)
            
            # Generate noise and noisy images
            noise = torch.randn_like(batch)
            x_noisy = q_sample(batch, t, noise)
            
            # Predict noise
            pred_noise = model(x_noisy, t)
            loss = F.mse_loss(pred_noise, noise)
            
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            
            step += 1
            
            if step % 100 == 0:
                print(f"Step {step}/{MAX_STEPS}, Loss: {loss.item():.4f}")
            
            # Generate samples
            if step % SAMPLE_EVERY == 0:
                print(f"Generating samples at step {step}...")
                samples = sample(model, n_samples=64)
                samples = (samples + 1) / 2  # Scale back to [0, 1]
                samples = samples.clamp(0, 1)
                
                grid = make_grid(samples, nrow=8, padding=2)
                save_path = OUT_DIR / f"samples_step_{step:05d}.png"
                save_image(grid, save_path)
                print(f"Saved samples to {save_path}")
    
    print("Training complete!")
    
    # Final samples
    print("Generating final samples...")
    samples = sample(model, n_samples=64)
    samples = (samples + 1) / 2
    samples = samples.clamp(0, 1)
    grid = make_grid(samples, nrow=8, padding=2)
    final_path = OUT_DIR / "samples_final.png"
    save_image(grid, final_path)
    print(f"Final samples saved to {final_path}")
    
    return final_path

if __name__ == "__main__":
    final_path = train()
    print(f"\n=== DONE ===\nFinal sample grid: {final_path}")
