#!/usr/bin/env python3
"""
Minimal DDPM implementation for MNIST - Tiny version (~500K params)
"""

import time
import math
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
from torchvision import datasets, transforms
from torchvision.utils import save_image, make_grid
import os

# Hyperparameters
TIMESTEPS = 1000
EPOCHS = 15
BATCH_SIZE = 128
LR = 2e-4
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
IMG_SIZE = 28
CHANNELS = 1

# Linear noise schedule
def linear_beta_schedule(timesteps):
    beta_start = 1e-4
    beta_end = 0.02
    return torch.linspace(beta_start, beta_end, timesteps)

# Precompute schedule values
betas = linear_beta_schedule(TIMESTEPS)
alphas = 1.0 - betas
alphas_cumprod = torch.cumprod(alphas, dim=0)
alphas_cumprod_prev = F.pad(alphas_cumprod[:-1], (1, 0), value=1.0)
sqrt_alphas_cumprod = torch.sqrt(alphas_cumprod)
sqrt_one_minus_alphas_cumprod = torch.sqrt(1.0 - alphas_cumprod)
sqrt_recip_alphas = torch.sqrt(1.0 / alphas)
posterior_variance = betas * (1.0 - alphas_cumprod_prev) / (1.0 - alphas_cumprod)

# Move to device
betas = betas.to(DEVICE)
sqrt_alphas_cumprod = sqrt_alphas_cumprod.to(DEVICE)
sqrt_one_minus_alphas_cumprod = sqrt_one_minus_alphas_cumprod.to(DEVICE)
sqrt_recip_alphas = sqrt_recip_alphas.to(DEVICE)
posterior_variance = posterior_variance.to(DEVICE)

def extract(a, t, x_shape):
    batch_size = t.shape[0]
    out = a.gather(-1, t)
    return out.reshape(batch_size, *((1,) * (len(x_shape) - 1)))

def q_sample(x_start, t, noise=None):
    if noise is None:
        noise = torch.randn_like(x_start)
    sqrt_alphas_cumprod_t = extract(sqrt_alphas_cumprod, t, x_start.shape)
    sqrt_one_minus_alphas_cumprod_t = extract(sqrt_one_minus_alphas_cumprod, t, x_start.shape)
    return sqrt_alphas_cumprod_t * x_start + sqrt_one_minus_alphas_cumprod_t * noise

class SinusoidalPositionEmbeddings(nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.dim = dim

    def forward(self, time):
        device = time.device
        half_dim = self.dim // 2
        embeddings = math.log(10000) / (half_dim - 1)
        embeddings = torch.exp(torch.arange(half_dim, device=device) * -embeddings)
        embeddings = time[:, None] * embeddings[None, :]
        embeddings = torch.cat((embeddings.sin(), embeddings.cos()), dim=-1)
        return embeddings

class ResBlock(nn.Module):
    def __init__(self, in_ch, out_ch, time_emb_dim):
        super().__init__()
        self.conv1 = nn.Conv2d(in_ch, out_ch, 3, padding=1)
        self.conv2 = nn.Conv2d(out_ch, out_ch, 3, padding=1)
        self.time_mlp = nn.Linear(time_emb_dim, out_ch)
        self.norm1 = nn.GroupNorm(min(8, out_ch), out_ch)
        self.norm2 = nn.GroupNorm(min(8, out_ch), out_ch)
        self.shortcut = nn.Conv2d(in_ch, out_ch, 1) if in_ch != out_ch else nn.Identity()

    def forward(self, x, t_emb):
        h = self.norm1(F.silu(self.conv1(x)))
        h = h + self.time_mlp(t_emb)[:, :, None, None]
        h = self.norm2(F.silu(self.conv2(h)))
        return h + self.shortcut(x)

class TinyUNet(nn.Module):
    def __init__(self, in_channels=1, time_emb_dim=32):
        super().__init__()
        
        # Smaller channels: 16 -> 32 -> 48 -> 64
        self.time_mlp = nn.Sequential(
            SinusoidalPositionEmbeddings(time_emb_dim),
            nn.Linear(time_emb_dim, time_emb_dim * 2),
            nn.SiLU(),
            nn.Linear(time_emb_dim * 2, time_emb_dim),
        )
        
        # Encoder (4 down blocks)
        self.conv_in = nn.Conv2d(in_channels, 16, 3, padding=1)
        self.down1 = ResBlock(16, 16, time_emb_dim)
        self.pool1 = nn.MaxPool2d(2)  # 28->14
        self.down2 = ResBlock(16, 32, time_emb_dim)
        self.pool2 = nn.MaxPool2d(2)  # 14->7
        self.down3 = ResBlock(32, 48, time_emb_dim)
        self.pool3 = nn.MaxPool2d(2, ceil_mode=True)  # 7->4
        self.down4 = ResBlock(48, 64, time_emb_dim)
        
        # Bottleneck
        self.mid = ResBlock(64, 64, time_emb_dim)
        
        # Decoder (4 up blocks)
        self.up4 = nn.ConvTranspose2d(64, 48, 2, stride=2)
        self.dec4 = ResBlock(96, 48, time_emb_dim)  # 48+48
        self.up3 = nn.ConvTranspose2d(48, 32, 2, stride=2)
        self.dec3 = ResBlock(64, 32, time_emb_dim)  # 32+32
        self.up2 = nn.ConvTranspose2d(32, 16, 2, stride=2)
        self.dec2 = ResBlock(32, 16, time_emb_dim)  # 16+16
        self.dec1 = ResBlock(32, 16, time_emb_dim)  # 16+16
        
        self.conv_out = nn.Conv2d(16, in_channels, 1)

    def forward(self, x, t):
        t_emb = self.time_mlp(t)
        
        # Encoder
        x1 = self.conv_in(x)
        x1 = self.down1(x1, t_emb)  # 28x28, 16ch
        x2 = self.pool1(x1)
        x2 = self.down2(x2, t_emb)  # 14x14, 32ch
        x3 = self.pool2(x2)
        x3 = self.down3(x3, t_emb)  # 7x7, 48ch
        x4 = self.pool3(x3)
        x4 = self.down4(x4, t_emb)  # 4x4, 64ch
        
        # Bottleneck
        x4 = self.mid(x4, t_emb)
        
        # Decoder with skip connections
        h = self.up4(x4)[:, :, :7, :7]  # Crop to 7x7
        h = self.dec4(torch.cat([h, x3], dim=1), t_emb)
        h = self.up3(h)
        h = self.dec3(torch.cat([h, x2], dim=1), t_emb)
        h = self.up2(h)
        h = self.dec2(torch.cat([h, x1], dim=1), t_emb)
        h = self.dec1(torch.cat([h, x1], dim=1), t_emb)
        
        return self.conv_out(h)

@torch.no_grad()
def p_sample(model, x, t, t_index):
    betas_t = extract(betas, t, x.shape)
    sqrt_one_minus_alphas_cumprod_t = extract(sqrt_one_minus_alphas_cumprod, t, x.shape)
    sqrt_recip_alphas_t = extract(sqrt_recip_alphas, t, x.shape)
    
    model_mean = sqrt_recip_alphas_t * (
        x - betas_t * model(x, t) / sqrt_one_minus_alphas_cumprod_t
    )
    
    if t_index == 0:
        return model_mean
    else:
        posterior_variance_t = extract(posterior_variance, t, x.shape)
        noise = torch.randn_like(x)
        return model_mean + torch.sqrt(posterior_variance_t) * noise

@torch.no_grad()
def sample(model, n_samples, img_size=28, channels=1):
    model.eval()
    shape = (n_samples, channels, img_size, img_size)
    x = torch.randn(shape, device=DEVICE)
    
    for i in reversed(range(TIMESTEPS)):
        t = torch.full((n_samples,), i, device=DEVICE, dtype=torch.long)
        x = p_sample(model, x, t, i)
    
    model.train()
    return x

def train():
    print(f"Using device: {DEVICE}")
    
    # Load MNIST
    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.5,), (0.5,))
    ])
    dataset = datasets.MNIST(root="./data", train=True, download=True, transform=transform)
    dataloader = DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=True, num_workers=2, pin_memory=True)
    
    # Create model
    model = TinyUNet(in_channels=CHANNELS).to(DEVICE)
    
    # Count parameters
    total_params = sum(p.numel() for p in model.parameters())
    print(f"Total parameters: {total_params:,}")
    
    optimizer = torch.optim.AdamW(model.parameters(), lr=LR)
    
    # Training loop
    print(f"Starting training for {EPOCHS} epochs...")
    start_time = time.time()
    
    for epoch in range(EPOCHS):
        epoch_loss = 0
        for batch_idx, (images, _) in enumerate(dataloader):
            images = images.to(DEVICE)
            batch_size = images.shape[0]
            
            t = torch.randint(0, TIMESTEPS, (batch_size,), device=DEVICE).long()
            noise = torch.randn_like(images)
            x_noisy = q_sample(images, t, noise)
            predicted_noise = model(x_noisy, t)
            loss = F.mse_loss(predicted_noise, noise)
            
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            
            epoch_loss += loss.item()
        
        avg_loss = epoch_loss / len(dataloader)
        elapsed = time.time() - start_time
        print(f"Epoch {epoch+1}/{EPOCHS} | Loss: {avg_loss:.4f} | Elapsed: {elapsed:.1f}s")
    
    total_time = time.time() - start_time
    print(f"\nTraining completed in {total_time:.2f} seconds ({total_time/60:.2f} minutes)")
    
    # Generate 8x8 sample grid
    print("Generating 8x8 sample grid...")
    samples = sample(model, n_samples=64)
    samples = (samples + 1) / 2
    samples = samples.clamp(0, 1)
    
    grid = make_grid(samples, nrow=8, padding=2)
    save_image(grid, "samples_8x8_tiny.png")
    print("Saved sample grid to samples_8x8_tiny.png")
    
    # Save model
    torch.save(model.state_dict(), "model_tiny.pt")
    print("Saved model to model_tiny.pt")
    
    # Write results
    with open("results_tiny.txt", "w") as f:
        f.write(f"DDPM MNIST Training Results (Tiny Model)\n")
        f.write(f"{'='*40}\n")
        f.write(f"Device: {DEVICE}\n")
        f.write(f"Total parameters: {total_params:,}\n")
        f.write(f"Epochs: {EPOCHS}\n")
        f.write(f"Batch size: {BATCH_SIZE}\n")
        f.write(f"Learning rate: {LR}\n")
        f.write(f"Timesteps: {TIMESTEPS}\n")
        f.write(f"Final loss: {avg_loss:.4f}\n")
        f.write(f"Total training time: {total_time:.2f} seconds ({total_time/60:.2f} minutes)\n")
    
    return total_time, total_params, avg_loss

if __name__ == "__main__":
    train()
