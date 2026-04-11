#!/usr/bin/env python3
"""
Minimal DDPM for Procedural Shapes
Trains on synthetic 32x32 RGB images of circles, squares, and triangles.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
import numpy as np
from PIL import Image, ImageDraw
import os
import time
from pathlib import Path

# ============== Procedural Shape Generator ==============

class ProceduralShapesDataset(Dataset):
    """Generates random shapes (circles, squares, triangles) on-the-fly."""
    
    def __init__(self, size=10000, img_size=32):
        self.size = size
        self.img_size = img_size
        
    def __len__(self):
        return self.size
    
    def __getitem__(self, idx):
        img = Image.new('RGB', (self.img_size, self.img_size), color='black')
        draw = ImageDraw.Draw(img)
        
        # Random color (bright colors for visibility)
        color = tuple(np.random.randint(100, 256, 3).tolist())
        
        # Random shape type
        shape_type = np.random.choice(['circle', 'square', 'triangle'])
        
        # Random position and size
        size = np.random.randint(8, 20)
        cx = np.random.randint(size//2 + 2, self.img_size - size//2 - 2)
        cy = np.random.randint(size//2 + 2, self.img_size - size//2 - 2)
        
        if shape_type == 'circle':
            bbox = [cx - size//2, cy - size//2, cx + size//2, cy + size//2]
            draw.ellipse(bbox, fill=color)
        elif shape_type == 'square':
            bbox = [cx - size//2, cy - size//2, cx + size//2, cy + size//2]
            draw.rectangle(bbox, fill=color)
        else:  # triangle
            points = [
                (cx, cy - size//2),
                (cx - size//2, cy + size//2),
                (cx + size//2, cy + size//2)
            ]
            draw.polygon(points, fill=color)
        
        # Convert to tensor and normalize to [-1, 1]
        img_array = np.array(img).astype(np.float32) / 127.5 - 1.0
        img_tensor = torch.from_numpy(img_array).permute(2, 0, 1)
        
        return img_tensor


# ============== U-Net for DDPM ==============

class SinusoidalPosEmb(nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.dim = dim
    
    def forward(self, t):
        device = t.device
        half_dim = self.dim // 2
        emb = np.log(10000) / (half_dim - 1)
        emb = torch.exp(torch.arange(half_dim, device=device) * -emb)
        emb = t[:, None] * emb[None, :]
        emb = torch.cat([torch.sin(emb), torch.cos(emb)], dim=-1)
        return emb


class ResBlock(nn.Module):
    def __init__(self, in_ch, out_ch, time_emb_dim):
        super().__init__()
        self.conv1 = nn.Conv2d(in_ch, out_ch, 3, padding=1)
        self.conv2 = nn.Conv2d(out_ch, out_ch, 3, padding=1)
        self.time_mlp = nn.Linear(time_emb_dim, out_ch)
        self.norm1 = nn.GroupNorm(8, out_ch)
        self.norm2 = nn.GroupNorm(8, out_ch)
        
        if in_ch != out_ch:
            self.shortcut = nn.Conv2d(in_ch, out_ch, 1)
        else:
            self.shortcut = nn.Identity()
    
    def forward(self, x, t_emb):
        h = self.conv1(x)
        h = self.norm1(h)
        h = F.silu(h)
        
        # Add time embedding
        h = h + self.time_mlp(t_emb)[:, :, None, None]
        
        h = self.conv2(h)
        h = self.norm2(h)
        h = F.silu(h)
        
        return h + self.shortcut(x)


class SimpleUNet(nn.Module):
    """Minimal U-Net for 32x32 images."""
    
    def __init__(self, in_ch=3, base_ch=64, time_emb_dim=128):
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
        
        # Bottleneck
        self.bot = ResBlock(base_ch * 4, base_ch * 4, time_emb_dim)
        
        # Decoder
        self.dec3 = ResBlock(base_ch * 8, base_ch * 2, time_emb_dim)
        self.dec2 = ResBlock(base_ch * 4, base_ch, time_emb_dim)
        self.dec1 = ResBlock(base_ch * 2, base_ch, time_emb_dim)
        
        self.final = nn.Conv2d(base_ch, in_ch, 1)
        
        self.pool = nn.MaxPool2d(2)
        self.up = nn.Upsample(scale_factor=2, mode='nearest')
    
    def forward(self, x, t):
        t_emb = self.time_mlp(t)
        
        # Encoder
        e1 = self.enc1(x, t_emb)        # 32x32
        e2 = self.enc2(self.pool(e1), t_emb)  # 16x16
        e3 = self.enc3(self.pool(e2), t_emb)  # 8x8
        
        # Bottleneck
        b = self.bot(self.pool(e3), t_emb)    # 4x4
        
        # Decoder with skip connections
        d3 = self.dec3(torch.cat([self.up(b), e3], dim=1), t_emb)   # 8x8
        d2 = self.dec2(torch.cat([self.up(d3), e2], dim=1), t_emb)  # 16x16
        d1 = self.dec1(torch.cat([self.up(d2), e1], dim=1), t_emb)  # 32x32
        
        return self.final(d1)


# ============== DDPM ==============

class DDPM:
    def __init__(self, model, timesteps=1000, device='cuda'):
        self.model = model
        self.timesteps = timesteps
        self.device = device
        
        # Linear schedule
        self.betas = torch.linspace(1e-4, 0.02, timesteps).to(device)
        self.alphas = 1. - self.betas
        self.alphas_cumprod = torch.cumprod(self.alphas, dim=0)
        self.sqrt_alphas_cumprod = torch.sqrt(self.alphas_cumprod)
        self.sqrt_one_minus_alphas_cumprod = torch.sqrt(1. - self.alphas_cumprod)
    
    def q_sample(self, x0, t, noise=None):
        """Forward diffusion process - add noise."""
        if noise is None:
            noise = torch.randn_like(x0)
        
        sqrt_alpha = self.sqrt_alphas_cumprod[t][:, None, None, None]
        sqrt_one_minus_alpha = self.sqrt_one_minus_alphas_cumprod[t][:, None, None, None]
        
        return sqrt_alpha * x0 + sqrt_one_minus_alpha * noise
    
    def p_losses(self, x0):
        """Compute training loss."""
        batch_size = x0.shape[0]
        t = torch.randint(0, self.timesteps, (batch_size,), device=self.device)
        noise = torch.randn_like(x0)
        
        x_noisy = self.q_sample(x0, t, noise)
        predicted_noise = self.model(x_noisy, t.float())
        
        return F.mse_loss(predicted_noise, noise)
    
    @torch.no_grad()
    def p_sample(self, x, t):
        """Single denoising step."""
        t_tensor = torch.full((x.shape[0],), t, device=self.device, dtype=torch.float)
        
        beta = self.betas[t]
        alpha = self.alphas[t]
        alpha_cumprod = self.alphas_cumprod[t]
        
        predicted_noise = self.model(x, t_tensor)
        
        mean = (1 / torch.sqrt(alpha)) * (
            x - (beta / self.sqrt_one_minus_alphas_cumprod[t]) * predicted_noise
        )
        
        if t > 0:
            noise = torch.randn_like(x)
            return mean + torch.sqrt(beta) * noise
        return mean
    
    @torch.no_grad()
    def sample(self, shape):
        """Generate samples from noise."""
        x = torch.randn(shape, device=self.device)
        
        for t in reversed(range(self.timesteps)):
            x = self.p_sample(x, t)
        
        return x


def save_samples(samples, path, nrow=4):
    """Save a grid of samples."""
    samples = (samples.clamp(-1, 1) + 1) / 2 * 255
    samples = samples.permute(0, 2, 3, 1).cpu().numpy().astype(np.uint8)
    
    n = samples.shape[0]
    ncol = (n + nrow - 1) // nrow
    
    grid = Image.new('RGB', (nrow * 32 + (nrow-1)*2, ncol * 32 + (ncol-1)*2), color='gray')
    
    for i, sample in enumerate(samples):
        row, col = i // nrow, i % nrow
        img = Image.fromarray(sample)
        grid.paste(img, (col * 34, row * 34))
    
    grid.save(path)
    return path


def main():
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f"Using device: {device}")
    
    # Create output directory
    output_dir = Path(__file__).parent / 'outputs'
    output_dir.mkdir(exist_ok=True)
    
    # Dataset and dataloader
    dataset = ProceduralShapesDataset(size=50000)
    dataloader = DataLoader(dataset, batch_size=64, shuffle=True, num_workers=2)
    
    # Save some training examples
    train_examples = torch.stack([dataset[i] for i in range(16)])
    save_samples(train_examples, output_dir / 'training_examples.png')
    print(f"Saved training examples to {output_dir / 'training_examples.png'}")
    
    # Model and DDPM
    model = SimpleUNet(in_ch=3, base_ch=64).to(device)
    ddpm = DDPM(model, timesteps=1000, device=device)
    
    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-4)
    
    # Training loop - ~5 minutes
    max_time = 5 * 60  # 5 minutes
    start_time = time.time()
    step = 0
    epoch = 0
    
    print("Starting training...")
    model.train()
    
    while time.time() - start_time < max_time:
        epoch += 1
        for batch in dataloader:
            if time.time() - start_time >= max_time:
                break
                
            batch = batch.to(device)
            
            optimizer.zero_grad()
            loss = ddpm.p_losses(batch)
            loss.backward()
            optimizer.step()
            
            step += 1
            
            if step % 100 == 0:
                elapsed = time.time() - start_time
                print(f"Step {step}, Epoch {epoch}, Loss: {loss.item():.4f}, Time: {elapsed:.1f}s")
    
    total_time = time.time() - start_time
    print(f"\nTraining complete! Steps: {step}, Time: {total_time:.1f}s")
    
    # Generate samples
    print("\nGenerating samples...")
    model.eval()
    
    # Generate multiple batches
    all_samples = []
    for i in range(2):
        samples = ddpm.sample((8, 3, 32, 32))
        all_samples.append(samples)
    
    samples = torch.cat(all_samples, dim=0)
    
    # Save samples
    sample_path = save_samples(samples, output_dir / 'generated_samples.png')
    print(f"Saved generated samples to {sample_path}")
    
    # Save model checkpoint
    torch.save({
        'model_state_dict': model.state_dict(),
        'optimizer_state_dict': optimizer.state_dict(),
        'step': step,
        'epoch': epoch,
    }, output_dir / 'ddpm_checkpoint.pt')
    print(f"Saved model checkpoint to {output_dir / 'ddpm_checkpoint.pt'}")
    
    print("\n=== ARTIFACTS ===")
    print(f"Training examples: {output_dir / 'training_examples.png'}")
    print(f"Generated samples: {output_dir / 'generated_samples.png'}")
    print(f"Model checkpoint: {output_dir / 'ddpm_checkpoint.pt'}")


if __name__ == '__main__':
    main()
