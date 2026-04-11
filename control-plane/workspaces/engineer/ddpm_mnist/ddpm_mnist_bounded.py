#!/usr/bin/env python3
"""
Minimal DDPM for MNIST - Bounded wall time version (<=200s target)
4-channel U-Net, 2 res blocks, tiny architecture
"""

import time
import math
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Subset
from torchvision import datasets, transforms
from torchvision.utils import save_image, make_grid
import os

# === Config ===
MAX_WALL_TIME = 180  # seconds - leave buffer for sampling
TIMESTEPS = 1000
BATCH_SIZE = 128
LR = 2e-4
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
IMG_SIZE = 28
CHANNELS = 1
BASE_CH = 4  # 4-channel U-Net as specified

# Output paths
OUT_DIR = "/home/ubuntu/.openclaw/workspace/control-plane/workspaces/engineer/ddpm_mnist/run_bounded"
os.makedirs(OUT_DIR, exist_ok=True)

# === Noise schedule ===
def linear_beta_schedule(timesteps):
    return torch.linspace(1e-4, 0.02, timesteps)

betas = linear_beta_schedule(TIMESTEPS).to(DEVICE)
alphas = 1.0 - betas
alphas_cumprod = torch.cumprod(alphas, dim=0)
alphas_cumprod_prev = F.pad(alphas_cumprod[:-1], (1, 0), value=1.0)
sqrt_alphas_cumprod = torch.sqrt(alphas_cumprod)
sqrt_one_minus_alphas_cumprod = torch.sqrt(1.0 - alphas_cumprod)
sqrt_recip_alphas = torch.sqrt(1.0 / alphas)
posterior_variance = betas * (1.0 - alphas_cumprod_prev) / (1.0 - alphas_cumprod)

def extract(a, t, x_shape):
    return a.gather(-1, t).reshape(t.shape[0], *((1,) * (len(x_shape) - 1)))

def q_sample(x_start, t, noise=None):
    if noise is None:
        noise = torch.randn_like(x_start)
    return (extract(sqrt_alphas_cumprod, t, x_start.shape) * x_start +
            extract(sqrt_one_minus_alphas_cumprod, t, x_start.shape) * noise)

# === Model ===
class SinusoidalPosEmb(nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.dim = dim

    def forward(self, t):
        half = self.dim // 2
        emb = math.log(10000) / (half - 1)
        emb = torch.exp(torch.arange(half, device=t.device) * -emb)
        emb = t[:, None] * emb[None, :]
        return torch.cat([emb.sin(), emb.cos()], dim=-1)

class ResBlock(nn.Module):
    def __init__(self, in_ch, out_ch, t_dim):
        super().__init__()
        self.conv1 = nn.Conv2d(in_ch, out_ch, 3, padding=1)
        self.conv2 = nn.Conv2d(out_ch, out_ch, 3, padding=1)
        self.t_proj = nn.Linear(t_dim, out_ch)
        self.norm1 = nn.GroupNorm(min(4, out_ch), out_ch)
        self.norm2 = nn.GroupNorm(min(4, out_ch), out_ch)
        self.skip = nn.Conv2d(in_ch, out_ch, 1) if in_ch != out_ch else nn.Identity()

    def forward(self, x, t):
        h = self.norm1(F.silu(self.conv1(x)))
        h = h + self.t_proj(t)[:, :, None, None]
        h = self.norm2(F.silu(self.conv2(h)))
        return h + self.skip(x)

class TinyUNet(nn.Module):
    """4-channel base, 2 res blocks total (1 down, 1 up)"""
    def __init__(self, in_ch=1, base_ch=4, t_dim=32):
        super().__init__()
        self.t_mlp = nn.Sequential(
            SinusoidalPosEmb(t_dim),
            nn.Linear(t_dim, t_dim * 2),
            nn.SiLU(),
            nn.Linear(t_dim * 2, t_dim),
        )
        
        ch1 = base_ch * 2   # 8
        ch2 = base_ch * 4   # 16
        
        # Encoder
        self.conv_in = nn.Conv2d(in_ch, ch1, 3, padding=1)
        self.down1 = ResBlock(ch1, ch1, t_dim)   # Block 1
        self.pool1 = nn.MaxPool2d(2)  # 28->14
        self.down2 = ResBlock(ch1, ch2, t_dim)   # Block 2
        self.pool2 = nn.MaxPool2d(2)  # 14->7
        
        # Middle
        self.mid = ResBlock(ch2, ch2, t_dim)
        
        # Decoder - note: after concat we have ch2 + ch2 = 2*ch2 for first, ch1 + ch1 = 2*ch1 for second
        self.up2 = nn.ConvTranspose2d(ch2, ch2, 2, stride=2)  # 7->14
        self.dec2 = ResBlock(ch2 + ch2, ch1, t_dim)           # 32 -> 8
        self.up1 = nn.ConvTranspose2d(ch1, ch1, 2, stride=2)  # 14->28
        self.dec1 = ResBlock(ch1 + ch1, ch1, t_dim)           # 16 -> 8
        
        self.conv_out = nn.Conv2d(ch1, in_ch, 1)

    def forward(self, x, t):
        t_emb = self.t_mlp(t)
        
        # Encoder path with skip connections
        x0 = self.conv_in(x)           # [B, 8, 28, 28]
        x1 = self.down1(x0, t_emb)     # [B, 8, 28, 28] - skip 1
        x2 = self.pool1(x1)            # [B, 8, 14, 14]
        x2 = self.down2(x2, t_emb)     # [B, 16, 14, 14] - skip 2
        x3 = self.pool2(x2)            # [B, 16, 7, 7]
        
        # Middle
        h = self.mid(x3, t_emb)        # [B, 16, 7, 7]
        
        # Decoder with skip connections
        h = self.up2(h)                # [B, 16, 14, 14]
        h = self.dec2(torch.cat([h, x2], dim=1), t_emb)  # [B, 32, 14, 14] -> [B, 8, 14, 14]
        h = self.up1(h)                # [B, 8, 28, 28]
        h = self.dec1(torch.cat([h, x1], dim=1), t_emb)  # [B, 16, 28, 28] -> [B, 8, 28, 28]
        
        return self.conv_out(h)        # [B, 1, 28, 28]

@torch.no_grad()
def p_sample(model, x, t, t_idx):
    beta_t = extract(betas, t, x.shape)
    sqrt_omc_t = extract(sqrt_one_minus_alphas_cumprod, t, x.shape)
    sqrt_ra_t = extract(sqrt_recip_alphas, t, x.shape)
    
    mean = sqrt_ra_t * (x - beta_t * model(x, t) / sqrt_omc_t)
    
    if t_idx == 0:
        return mean
    pv_t = extract(posterior_variance, t, x.shape)
    return mean + torch.sqrt(pv_t) * torch.randn_like(x)

@torch.no_grad()
def sample(model, n, steps_to_show=8):
    """Generate samples and return intermediate steps for visualization"""
    model.eval()
    x = torch.randn(n, CHANNELS, IMG_SIZE, IMG_SIZE, device=DEVICE)
    
    # Save intermediate steps
    step_indices = [int(TIMESTEPS * (1 - i/(steps_to_show-1))) for i in range(steps_to_show)]
    step_indices[-1] = 0  # ensure we capture final
    intermediates = []
    
    for i in reversed(range(TIMESTEPS)):
        t = torch.full((n,), i, device=DEVICE, dtype=torch.long)
        x = p_sample(model, x, t, i)
        if i in step_indices:
            intermediates.append(x.clone())
    
    model.train()
    return x, intermediates[::-1]  # reverse to get noise -> clean order

def main():
    print(f"Device: {DEVICE}")
    print(f"Max wall time: {MAX_WALL_TIME}s")
    
    # Data
    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.5,), (0.5,))
    ])
    dataset = datasets.MNIST(root="./data", train=True, download=True, transform=transform)
    # Use subset for faster iteration
    subset = Subset(dataset, range(30000))
    loader = DataLoader(subset, batch_size=BATCH_SIZE, shuffle=True, num_workers=2, pin_memory=True)
    
    # Model
    model = TinyUNet(in_ch=CHANNELS, base_ch=BASE_CH).to(DEVICE)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"Parameters: {n_params:,} (base_ch={BASE_CH})")
    
    optimizer = torch.optim.AdamW(model.parameters(), lr=LR)
    
    # Training with wall-time bound
    start = time.time()
    epoch = 0
    total_batches = 0
    losses = []
    
    print("Training...")
    while True:
        epoch += 1
        epoch_loss = 0
        batches = 0
        
        for imgs, _ in loader:
            if time.time() - start > MAX_WALL_TIME:
                break
                
            imgs = imgs.to(DEVICE)
            t = torch.randint(0, TIMESTEPS, (imgs.shape[0],), device=DEVICE)
            noise = torch.randn_like(imgs)
            x_noisy = q_sample(imgs, t, noise)
            pred = model(x_noisy, t)
            loss = F.mse_loss(pred, noise)
            
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            
            epoch_loss += loss.item()
            batches += 1
            total_batches += 1
        
        if batches > 0:
            avg = epoch_loss / batches
            losses.append(avg)
            elapsed = time.time() - start
            print(f"Epoch {epoch} | Loss: {avg:.4f} | Elapsed: {elapsed:.1f}s")
        
        if time.time() - start > MAX_WALL_TIME:
            print(f"Wall time limit reached after {epoch} epochs")
            break
    
    train_time = time.time() - start
    final_loss = losses[-1] if losses else float('nan')
    
    # Generate samples with denoising progression
    print("Generating samples with denoising progression...")
    sample_start = time.time()
    
    # Generate 8 samples showing denoising steps
    samples, intermediates = sample(model, n=8, steps_to_show=8)
    
    # Create progression grid: rows = denoising steps, each row has 8 samples
    progression_rows = []
    for step_imgs in intermediates:
        step_imgs = (step_imgs + 1) / 2  # denormalize
        step_imgs = step_imgs.clamp(0, 1)
        progression_rows.append(step_imgs)
    
    # Stack all images: [n_steps * n_samples, 1, 28, 28]
    all_imgs = torch.cat(progression_rows, dim=0)
    grid = make_grid(all_imgs, nrow=8, padding=2)  # 8 samples per row
    save_image(grid, f"{OUT_DIR}/denoising_progression.png")
    
    # Also save final samples only (larger grid)
    final_samples, _ = sample(model, n=64, steps_to_show=2)
    final = (final_samples + 1) / 2
    final = final.clamp(0, 1)
    save_image(make_grid(final, nrow=8), f"{OUT_DIR}/final_samples_8x8.png")
    
    sample_time = time.time() - sample_start
    total_time = time.time() - start
    
    # Save checkpoint
    torch.save({
        'model_state': model.state_dict(),
        'epochs': epoch,
        'loss': final_loss,
        'params': n_params,
    }, f"{OUT_DIR}/checkpoint.pt")
    
    # Write results
    results = f"""MNIST DDPM Training - Bounded Run
{'='*40}
Device: {DEVICE}
Base channels: {BASE_CH}
Parameters: {n_params:,}
Epochs completed: {epoch}
Total batches: {total_batches}
Final loss: {final_loss:.4f}
Training time: {train_time:.1f}s
Sampling time: {sample_time:.1f}s
Total time: {total_time:.1f}s

Artifacts:
- {OUT_DIR}/denoising_progression.png (8 samples x denoising steps)
- {OUT_DIR}/final_samples_8x8.png (64 final samples)
- {OUT_DIR}/checkpoint.pt
"""
    print(results)
    
    with open(f"{OUT_DIR}/results.txt", "w") as f:
        f.write(results)
    
    print(f"\nDone! Total wall time: {total_time:.1f}s")
    return total_time, epoch, final_loss

if __name__ == "__main__":
    main()
