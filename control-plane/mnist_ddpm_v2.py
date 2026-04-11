#!/usr/bin/env python3
"""Improved DDPM for MNIST - better convergence."""

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
from torchvision import datasets, transforms
from torchvision.utils import save_image
import time
import os
import math

# Use GPU 1 (less loaded)
os.environ["CUDA_VISIBLE_DEVICES"] = "1"

# Ensure we're in the right directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SCRIPT_DIR)

# Config - tuned for quality within 10 min
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
TIMESTEPS = 100
EPOCHS = 15  # More epochs for better convergence
BATCH_SIZE = 128
LR = 1e-3
IMG_SIZE = 28
CHANNELS = 1

print(f"Training on: {DEVICE}")
print(f"Config: T={TIMESTEPS}, epochs={EPOCHS}, batch={BATCH_SIZE}, lr={LR}")

# Cosine beta schedule (better than linear)
def cosine_beta_schedule(timesteps, s=0.008):
    steps = timesteps + 1
    x = torch.linspace(0, timesteps, steps)
    alphas_cumprod = torch.cos(((x / timesteps) + s) / (1 + s) * math.pi * 0.5) ** 2
    alphas_cumprod = alphas_cumprod / alphas_cumprod[0]
    betas = 1 - (alphas_cumprod[1:] / alphas_cumprod[:-1])
    return torch.clip(betas, 0.0001, 0.9999)

beta = cosine_beta_schedule(TIMESTEPS).to(DEVICE)
alpha = 1 - beta
alpha_bar = torch.cumprod(alpha, dim=0)
alpha_bar_prev = F.pad(alpha_bar[:-1], (1, 0), value=1.0)

# Posterior variance
posterior_variance = beta * (1. - alpha_bar_prev) / (1. - alpha_bar)

def forward_diffusion(x0, t, noise=None):
    """Add noise to images."""
    if noise is None:
        noise = torch.randn_like(x0)
    sqrt_alpha_bar = alpha_bar[t].sqrt().view(-1, 1, 1, 1)
    sqrt_one_minus = (1 - alpha_bar[t]).sqrt().view(-1, 1, 1, 1)
    return sqrt_alpha_bar * x0 + sqrt_one_minus * noise, noise

class SinusoidalPositionEmbeddings(nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.dim = dim

    def forward(self, t):
        device = t.device
        half_dim = self.dim // 2
        embeddings = math.log(10000) / (half_dim - 1)
        embeddings = torch.exp(torch.arange(half_dim, device=device) * -embeddings)
        embeddings = t[:, None] * embeddings[None, :]
        embeddings = torch.cat((embeddings.sin(), embeddings.cos()), dim=-1)
        return embeddings

class ResBlock(nn.Module):
    def __init__(self, in_ch, out_ch, time_emb_dim):
        super().__init__()
        self.time_mlp = nn.Linear(time_emb_dim, out_ch)
        self.conv1 = nn.Conv2d(in_ch, out_ch, 3, padding=1)
        self.conv2 = nn.Conv2d(out_ch, out_ch, 3, padding=1)
        self.norm1 = nn.GroupNorm(8, out_ch)
        self.norm2 = nn.GroupNorm(8, out_ch)
        self.shortcut = nn.Conv2d(in_ch, out_ch, 1) if in_ch != out_ch else nn.Identity()

    def forward(self, x, t_emb):
        h = self.norm1(F.silu(self.conv1(x)))
        h = h + self.time_mlp(t_emb)[:, :, None, None]
        h = self.norm2(F.silu(self.conv2(h)))
        return h + self.shortcut(x)

class ImprovedUNet(nn.Module):
    """Better UNet with proper time embeddings and residual connections."""
    def __init__(self, ch=64, time_emb_dim=128):
        super().__init__()
        
        # Time embedding
        self.time_mlp = nn.Sequential(
            SinusoidalPositionEmbeddings(ch),
            nn.Linear(ch, time_emb_dim),
            nn.GELU(),
            nn.Linear(time_emb_dim, time_emb_dim),
        )
        
        # Encoder
        self.conv_in = nn.Conv2d(CHANNELS, ch, 3, padding=1)
        self.res1 = ResBlock(ch, ch, time_emb_dim)
        self.down1 = nn.Conv2d(ch, ch, 4, stride=2, padding=1)  # 28->14
        
        self.res2 = ResBlock(ch, ch*2, time_emb_dim)
        self.down2 = nn.Conv2d(ch*2, ch*2, 4, stride=2, padding=1)  # 14->7
        
        # Middle
        self.mid1 = ResBlock(ch*2, ch*2, time_emb_dim)
        self.mid2 = ResBlock(ch*2, ch*2, time_emb_dim)
        
        # Decoder
        self.up1 = nn.ConvTranspose2d(ch*2, ch*2, 4, stride=2, padding=1)  # 7->14
        self.res3 = ResBlock(ch*4, ch, time_emb_dim)  # concat with skip
        
        self.up2 = nn.ConvTranspose2d(ch, ch, 4, stride=2, padding=1)  # 14->28
        self.res4 = ResBlock(ch*2, ch, time_emb_dim)  # concat with skip
        
        self.conv_out = nn.Sequential(
            nn.GroupNorm(8, ch),
            nn.SiLU(),
            nn.Conv2d(ch, CHANNELS, 3, padding=1),
        )
        
    def forward(self, x, t):
        t_emb = self.time_mlp(t.float())
        
        # Encoder
        h1 = self.conv_in(x)
        h1 = self.res1(h1, t_emb)
        
        h2 = self.down1(h1)
        h2 = self.res2(h2, t_emb)
        
        # Middle
        h = self.down2(h2)
        h = self.mid1(h, t_emb)
        h = self.mid2(h, t_emb)
        
        # Decoder with skip connections
        h = self.up1(h)
        h = torch.cat([h, h2], dim=1)
        h = self.res3(h, t_emb)
        
        h = self.up2(h)
        h = torch.cat([h, h1], dim=1)
        h = self.res4(h, t_emb)
        
        return self.conv_out(h)

@torch.no_grad()
def sample(model, n_samples=64, seed=42):
    """Generate samples using DDPM sampling."""
    torch.manual_seed(seed)
    model.eval()
    
    x = torch.randn(n_samples, CHANNELS, IMG_SIZE, IMG_SIZE, device=DEVICE)
    
    for t in reversed(range(TIMESTEPS)):
        t_batch = torch.full((n_samples,), t, device=DEVICE, dtype=torch.long)
        
        # Predict noise
        predicted_noise = model(x, t_batch)
        
        # Compute x_{t-1}
        alpha_t = alpha[t]
        alpha_bar_t = alpha_bar[t]
        
        # Mean
        coef1 = 1 / alpha_t.sqrt()
        coef2 = beta[t] / (1 - alpha_bar_t).sqrt()
        mean = coef1 * (x - coef2 * predicted_noise)
        
        if t > 0:
            noise = torch.randn_like(x)
            sigma = posterior_variance[t].sqrt()
            x = mean + sigma * noise
        else:
            x = mean
    
    return x.clamp(-1, 1)

def main():
    start_time = time.time()
    
    # Data
    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize([0.5], [0.5])  # Scale to [-1, 1]
    ])
    
    dataset = datasets.MNIST('./data', train=True, download=True, transform=transform)
    dataloader = DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=True, 
                           num_workers=4, pin_memory=True, drop_last=True)
    
    # Model
    model = ImprovedUNet(ch=64).to(DEVICE)
    optimizer = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=0.01)
    
    # Learning rate scheduler - cosine annealing
    total_steps = EPOCHS * len(dataloader)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, total_steps, eta_min=1e-5)
    
    print(f"Model params: {sum(p.numel() for p in model.parameters()):,}")
    print(f"Training for {EPOCHS} epochs, {len(dataloader)} batches/epoch")
    
    # Training
    model.train()
    for epoch in range(EPOCHS):
        epoch_loss = 0
        for batch_idx, (images, _) in enumerate(dataloader):
            images = images.to(DEVICE)
            
            # Sample random timesteps
            t = torch.randint(0, TIMESTEPS, (images.shape[0],), device=DEVICE)
            
            # Add noise
            noise = torch.randn_like(images)
            noisy_images, _ = forward_diffusion(images, t, noise)
            
            # Predict noise
            predicted_noise = model(noisy_images, t)
            
            # MSE loss
            loss = F.mse_loss(predicted_noise, noise)
            
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            
            epoch_loss += loss.item()
        
        avg_loss = epoch_loss / len(dataloader)
        elapsed = time.time() - start_time
        print(f"Epoch {epoch+1}/{EPOCHS} | Loss: {avg_loss:.4f} | Time: {elapsed:.1f}s | LR: {scheduler.get_last_lr()[0]:.2e}")
        
        # Generate samples periodically
        if (epoch + 1) % 5 == 0 or epoch == EPOCHS - 1:
            samples = sample(model, n_samples=64)
            samples = (samples + 1) / 2  # Scale to [0, 1]
            save_image(samples, f'outputs/mnist_ddpm_epoch{epoch+1}.png', nrow=8)
            print(f"  Saved samples at epoch {epoch+1}")
            model.train()
    
    # Final samples
    total_time = time.time() - start_time
    print(f"\nTraining complete in {total_time:.1f}s ({total_time/60:.1f} min)")
    
    samples = sample(model, n_samples=64, seed=0)
    samples = (samples + 1) / 2
    save_image(samples, 'outputs/mnist_ddpm_samples.png', nrow=8)
    print("Final samples saved to outputs/mnist_ddpm_samples.png")
    
    # Also save a larger grid for better evaluation
    samples_large = sample(model, n_samples=100, seed=123)
    samples_large = (samples_large + 1) / 2
    save_image(samples_large, 'outputs/mnist_ddpm_final_grid.png', nrow=10)
    print("Large grid saved to outputs/mnist_ddpm_final_grid.png")
    
    # Save model
    torch.save(model.state_dict(), 'outputs/mnist_ddpm_model.pt')
    print("Model saved to outputs/mnist_ddpm_model.pt")

if __name__ == '__main__':
    main()
