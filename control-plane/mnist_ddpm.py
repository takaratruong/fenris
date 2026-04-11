#!/usr/bin/env python3
"""Minimal DDPM for MNIST - optimized for speed."""

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
from torchvision import datasets, transforms
from torchvision.utils import save_image
import time
import os

# Config - optimized for speed
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
TIMESTEPS = 50  # Fewer steps for speed
EPOCHS = 5
BATCH_SIZE = 256
LR = 2e-4
IMG_SIZE = 28
CHANNELS = 1

print(f"Training on: {DEVICE}")

# Beta schedule
beta = torch.linspace(1e-4, 0.02, TIMESTEPS, device=DEVICE)
alpha = 1 - beta
alpha_bar = torch.cumprod(alpha, dim=0)

def forward_diffusion(x0, t, noise=None):
    """Add noise to images."""
    if noise is None:
        noise = torch.randn_like(x0)
    sqrt_alpha_bar = alpha_bar[t].sqrt().view(-1, 1, 1, 1)
    sqrt_one_minus = (1 - alpha_bar[t]).sqrt().view(-1, 1, 1, 1)
    return sqrt_alpha_bar * x0 + sqrt_one_minus * noise, noise

class TinyUNet(nn.Module):
    """Minimal UNet for 28x28 images."""
    def __init__(self, ch=32):
        super().__init__()
        # Time embedding
        self.time_mlp = nn.Sequential(
            nn.Linear(1, ch * 4),
            nn.GELU(),
            nn.Linear(ch * 4, ch * 4)
        )
        
        # Encoder
        self.conv1 = nn.Conv2d(CHANNELS, ch, 3, padding=1)
        self.conv2 = nn.Conv2d(ch, ch, 3, padding=1)
        self.down1 = nn.Conv2d(ch, ch * 2, 4, stride=2, padding=1)  # 28->14
        self.conv3 = nn.Conv2d(ch * 2, ch * 2, 3, padding=1)
        self.down2 = nn.Conv2d(ch * 2, ch * 4, 4, stride=2, padding=1)  # 14->7
        
        # Middle
        self.mid1 = nn.Conv2d(ch * 4, ch * 4, 3, padding=1)
        self.mid2 = nn.Conv2d(ch * 4, ch * 4, 3, padding=1)
        
        # Decoder  
        self.up1 = nn.ConvTranspose2d(ch * 4, ch * 2, 4, stride=2, padding=1)  # 7->14
        self.conv4 = nn.Conv2d(ch * 4, ch * 2, 3, padding=1)  # skip connection
        self.up2 = nn.ConvTranspose2d(ch * 2, ch, 4, stride=2, padding=1)  # 14->28
        self.conv5 = nn.Conv2d(ch * 2, ch, 3, padding=1)  # skip connection
        
        # Output
        self.out = nn.Conv2d(ch, CHANNELS, 1)
        
        self.norm1 = nn.GroupNorm(8, ch)
        self.norm2 = nn.GroupNorm(8, ch * 2)
        self.norm3 = nn.GroupNorm(8, ch * 4)
        
    def forward(self, x, t):
        # Time embedding
        t_emb = self.time_mlp(t.float().view(-1, 1) / TIMESTEPS)
        
        # Encoder
        h1 = F.gelu(self.norm1(self.conv1(x)))
        h1 = F.gelu(self.norm1(self.conv2(h1)))
        
        h2 = F.gelu(self.norm2(self.down1(h1)))
        h2 = F.gelu(self.norm2(self.conv3(h2)))
        
        h3 = F.gelu(self.norm3(self.down2(h2)))
        
        # Middle with time conditioning
        h3 = h3 + t_emb.view(-1, t_emb.size(1), 1, 1).expand(-1, -1, h3.size(2), h3.size(3))[:, :h3.size(1)]
        h = F.gelu(self.norm3(self.mid1(h3)))
        h = F.gelu(self.norm3(self.mid2(h)))
        
        # Decoder with skip connections
        h = self.up1(h)
        h = torch.cat([h, h2], dim=1)
        h = F.gelu(self.norm2(self.conv4(h)))
        
        h = self.up2(h)
        h = torch.cat([h, h1], dim=1)
        h = F.gelu(self.norm1(self.conv5(h)))
        
        return self.out(h)

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
    print("Loading MNIST...")
    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize([0.5], [0.5])  # Scale to [-1, 1]
    ])
    
    dataset = datasets.MNIST('./data', train=True, download=True, transform=transform)
    loader = DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=True, num_workers=4, pin_memory=True)
    
    print(f"Dataset: {len(dataset)} images, {len(loader)} batches per epoch")
    
    model = TinyUNet(ch=32).to(DEVICE)
    optimizer = torch.optim.AdamW(model.parameters(), lr=LR)
    
    param_count = sum(p.numel() for p in model.parameters())
    print(f"Model parameters: {param_count:,}")
    
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
            optimizer.step()
            
            total_loss += loss.item()
            
            if batch_idx % 50 == 0:
                print(f"  Epoch {epoch+1}/{EPOCHS}, Batch {batch_idx}/{len(loader)}, Loss: {loss.item():.4f}")
        
        avg_loss = total_loss / len(loader)
        elapsed = time.time() - start_time
        print(f"Epoch {epoch+1}/{EPOCHS} complete. Avg Loss: {avg_loss:.4f}, Elapsed: {elapsed:.1f}s")
    
    total_time = time.time() - start_time
    print(f"\nTraining complete in {total_time:.1f}s")
    
    # Generate samples
    print("Generating samples...")
    samples = sample(model, n_samples=64)
    
    # Save grid
    output_dir = "/home/ubuntu/.openclaw/workspace/control-plane/outputs"
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "mnist_ddpm_samples.png")
    
    # Rescale to [0, 1] for saving
    samples = (samples + 1) / 2
    save_image(samples, output_path, nrow=8, padding=2)
    
    print(f"\n{'='*50}")
    print(f"RESULTS:")
    print(f"  Training time: {total_time:.1f}s")
    print(f"  Final loss: {avg_loss:.4f}")
    print(f"  Sample grid saved to: {output_path}")
    print(f"{'='*50}")
    
    return output_path, total_time, avg_loss

if __name__ == "__main__":
    output_path, train_time, final_loss = train()
