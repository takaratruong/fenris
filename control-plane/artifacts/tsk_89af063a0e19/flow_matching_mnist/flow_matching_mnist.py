#!/usr/bin/env python3
"""
Minimal Flow Matching / Rectified Flow implementation for MNIST.
Same tiny U-Net architecture as DDPM for fair comparison.

Flow Matching learns velocity v(x,t) transporting prior p_0 to data p_1.
Uses optimal transport conditional flow matching (OT-CFM):
  x_t = (1-t)*x_0 + t*x_1  (linear interpolation)
  v_target = x_1 - x_0     (target velocity)
  
Loss: MSE(v_theta(x_t, t), v_target)
"""

import time
import math
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
from torchvision import datasets, transforms
from torchvision.utils import save_image
from pathlib import Path


# ============================================================================
# Tiny U-Net (identical to DDPM for fair comparison)
# ============================================================================

class SinusoidalPositionEmbedding(nn.Module):
    """Sinusoidal timestep embedding."""
    
    def __init__(self, dim):
        super().__init__()
        self.dim = dim
    
    def forward(self, t):
        device = t.device
        half_dim = self.dim // 2
        emb = math.log(10000) / (half_dim - 1)
        emb = torch.exp(torch.arange(half_dim, device=device) * -emb)
        emb = t[:, None] * emb[None, :]
        emb = torch.cat([emb.sin(), emb.cos()], dim=-1)
        return emb


class ResBlock(nn.Module):
    """Simple residual block with time embedding."""
    
    def __init__(self, in_ch, out_ch, time_dim):
        super().__init__()
        self.conv1 = nn.Conv2d(in_ch, out_ch, 3, padding=1)
        self.conv2 = nn.Conv2d(out_ch, out_ch, 3, padding=1)
        self.time_mlp = nn.Linear(time_dim, out_ch)
        self.norm1 = nn.GroupNorm(1, out_ch)
        self.norm2 = nn.GroupNorm(1, out_ch)
        
        if in_ch != out_ch:
            self.skip = nn.Conv2d(in_ch, out_ch, 1)
        else:
            self.skip = nn.Identity()
    
    def forward(self, x, t_emb):
        h = self.conv1(x)
        h = self.norm1(h)
        h = F.silu(h)
        h = h + self.time_mlp(t_emb)[:, :, None, None]
        h = self.conv2(h)
        h = self.norm2(h)
        h = F.silu(h)
        return h + self.skip(x)


class TinyUNet(nn.Module):
    """
    Tiny U-Net for MNIST (28x28 grayscale).
    Base channels: 4, Down/Up blocks: 2
    Predicts VELOCITY (not noise) for flow matching.
    """
    
    def __init__(self, in_channels=1, base_channels=4, time_dim=16):
        super().__init__()
        
        ch = base_channels
        
        # Time embedding
        self.time_mlp = nn.Sequential(
            SinusoidalPositionEmbedding(time_dim),
            nn.Linear(time_dim, time_dim * 4),
            nn.SiLU(),
            nn.Linear(time_dim * 4, time_dim * 4),
        )
        time_dim_out = time_dim * 4
        
        # Encoder
        self.conv_in = nn.Conv2d(in_channels, ch, 3, padding=1)
        self.down1 = ResBlock(ch, ch * 2, time_dim_out)
        self.pool1 = nn.MaxPool2d(2)
        self.down2 = ResBlock(ch * 2, ch * 4, time_dim_out)
        self.pool2 = nn.MaxPool2d(2)
        
        # Bottleneck
        self.mid = ResBlock(ch * 4, ch * 4, time_dim_out)
        
        # Decoder
        self.up2 = nn.Upsample(scale_factor=2, mode='nearest')
        self.dec2 = ResBlock(ch * 4 + ch * 4, ch * 2, time_dim_out)
        self.up1 = nn.Upsample(scale_factor=2, mode='nearest')
        self.dec1 = ResBlock(ch * 2 + ch * 2, ch, time_dim_out)
        
        # Output
        self.conv_out = nn.Conv2d(ch, in_channels, 1)
    
    def forward(self, x, t):
        # t is in [0, 1] for flow matching, scale to match embedding range
        t_emb = self.time_mlp(t.float() * 1000)  # Scale to [0, 1000] range
        
        # Encoder
        x = self.conv_in(x)
        d1 = self.down1(x, t_emb)
        x = self.pool1(d1)
        d2 = self.down2(x, t_emb)
        x = self.pool2(d2)
        
        # Bottleneck
        x = self.mid(x, t_emb)
        
        # Decoder with skip connections
        x = self.up2(x)
        x = torch.cat([x, d2], dim=1)
        x = self.dec2(x, t_emb)
        x = self.up1(x)
        x = torch.cat([x, d1], dim=1)
        x = self.dec1(x, t_emb)
        
        return self.conv_out(x)


# ============================================================================
# Flow Matching Training
# ============================================================================

def train_epoch(model, dataloader, optimizer, device):
    """
    Train one epoch with OT-CFM objective.
    
    OT-CFM (Optimal Transport Conditional Flow Matching):
    - x_0 ~ N(0, I) (prior/noise)
    - x_1 ~ data
    - x_t = (1-t)*x_0 + t*x_1
    - v_target = x_1 - x_0
    - Loss = MSE(v_pred, v_target)
    """
    model.train()
    total_loss = 0.0
    num_batches = 0
    
    for images, _ in dataloader:
        x_1 = images.to(device)  # Data samples
        batch_size = x_1.shape[0]
        
        # Sample prior (standard Gaussian noise)
        x_0 = torch.randn_like(x_1)
        
        # Sample time uniformly in [0, 1]
        t = torch.rand(batch_size, device=device)
        
        # Linear interpolation: x_t = (1-t)*x_0 + t*x_1
        t_expand = t.view(-1, 1, 1, 1)
        x_t = (1 - t_expand) * x_0 + t_expand * x_1
        
        # Target velocity: direction from x_0 to x_1
        v_target = x_1 - x_0
        
        # Predict velocity
        v_pred = model(x_t, t)
        
        # MSE loss
        loss = F.mse_loss(v_pred, v_target)
        
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        
        total_loss += loss.item()
        num_batches += 1
    
    return total_loss / num_batches


# ============================================================================
# ODE Sampling (Euler method)
# ============================================================================

@torch.no_grad()
def sample_euler(model, n_samples=64, n_steps=50, img_size=28, device='cuda'):
    """
    Generate samples using Euler ODE integration.
    
    dx/dt = v_theta(x, t) from t=0 to t=1
    Starting from x_0 ~ N(0, I)
    """
    model.eval()
    
    # Start from prior (Gaussian noise)
    x = torch.randn(n_samples, 1, img_size, img_size, device=device)
    
    dt = 1.0 / n_steps
    
    for i in range(n_steps):
        t = torch.full((n_samples,), i * dt, device=device)
        v = model(x, t)
        x = x + v * dt
    
    return x


# ============================================================================
# Main
# ============================================================================

def main():
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    batch_size = 128
    lr = 1e-3
    base_channels = 4
    n_steps = 50  # ODE steps for sampling
    
    print(f"Device: {device}")
    print(f"Base channels: {base_channels}, ODE steps: {n_steps}")
    
    # Data
    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize([0.5], [0.5])
    ])
    
    dataset = datasets.MNIST(root='./data', train=True, download=True, transform=transform)
    dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=True, num_workers=0, pin_memory=True)
    
    print(f"Dataset size: {len(dataset)}, Batches per epoch: {len(dataloader)}")
    
    # Model
    model = TinyUNet(in_channels=1, base_channels=base_channels).to(device)
    num_params = sum(p.numel() for p in model.parameters())
    print(f"Model parameters: {num_params:,}")
    
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr)
    
    # Train 1 epoch
    print("\n--- Training 1 epoch (Flow Matching) ---")
    
    if device == 'cuda':
        torch.cuda.synchronize()
    start_time = time.time()
    
    avg_loss = train_epoch(model, dataloader, optimizer, device)
    
    if device == 'cuda':
        torch.cuda.synchronize()
    epoch_time = time.time() - start_time
    
    print(f"Epoch 1 - Loss: {avg_loss:.4f}, Time: {epoch_time:.2f}s")
    
    # Generate 8x8 sample grid (64 samples)
    print(f"\n--- Generating 8x8 sample grid ({n_steps} ODE steps) ---")
    start_sample = time.time()
    samples = sample_euler(model, n_samples=64, n_steps=n_steps, device=device)
    sample_time = time.time() - start_sample
    print(f"Generated 64 samples in {sample_time:.2f}s")
    
    # Save samples
    output_dir = Path(__file__).parent
    
    samples = (samples + 1) / 2  # [-1, 1] -> [0, 1]
    samples = samples.clamp(0, 1)
    
    save_image(samples, output_dir / 'fm_samples_8x8.png', nrow=8)
    print(f"Samples saved to {output_dir / 'fm_samples_8x8.png'}")
    
    # Save model
    torch.save({
        'model_state_dict': model.state_dict(),
        'optimizer_state_dict': optimizer.state_dict(),
        'epoch': 1,
        'loss': avg_loss,
    }, output_dir / 'flow_matching_mnist_epoch1.pt')
    
    # Summary
    print("\n" + "="*50)
    print("FLOW MATCHING BENCHMARK RESULTS")
    print("="*50)
    print(f"Hardware: {torch.cuda.get_device_name(0) if device == 'cuda' else 'CPU'}")
    print(f"Model: TinyUNet (base_ch={base_channels}, params={num_params:,})")
    print(f"Dataset: MNIST ({len(dataset)} samples)")
    print(f"Batch size: {batch_size}")
    print(f"1 Epoch training time: {epoch_time:.2f} seconds")
    print(f"Samples/second: {len(dataset) / epoch_time:.1f}")
    print(f"Final loss: {avg_loss:.4f}")
    print(f"ODE sampling steps: {n_steps}")
    print(f"Sample generation (64 imgs): {sample_time:.2f}s")
    print("="*50)
    
    return {
        'method': 'flow_matching',
        'epoch_time_seconds': epoch_time,
        'avg_loss': avg_loss,
        'num_params': num_params,
        'samples_per_second': len(dataset) / epoch_time,
        'ode_steps': n_steps,
        'sample_time_64': sample_time,
        'device': str(torch.cuda.get_device_name(0) if device == 'cuda' else 'CPU'),
    }


if __name__ == '__main__':
    results = main()
