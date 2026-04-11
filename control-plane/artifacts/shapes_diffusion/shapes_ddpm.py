#!/usr/bin/env python3
"""Minimal DDPM for 28x28 synthetic shapes. <200 lines, 5 min training."""
import numpy as np, torch, torch.nn as nn, torch.nn.functional as F, time, json
from torch.utils.data import Dataset, DataLoader
from pathlib import Path

def generate_shape(shape_type: str, size: int = 28) -> np.ndarray:
    img = np.zeros((size, size), dtype=np.float32)
    center, radius = size // 2, np.random.randint(4, 10)
    if shape_type == 'circle':
        y, x = np.ogrid[:size, :size]
        img[(x - center)**2 + (y - center)**2 <= radius**2] = 1.0
    elif shape_type == 'square':
        img[center-radius:center+radius, center-radius:center+radius] = 1.0
    elif shape_type == 'triangle':
        for row in range(radius * 2):
            w, y = row + 1, center - radius + row
            if 0 <= y < size:
                img[y, max(0, center - w//2):min(size, center - w//2 + w)] = 1.0
    return img

class ShapesDataset(Dataset):
    def __init__(self, n=3000):
        self.data = [torch.tensor(generate_shape(np.random.choice(['circle','square','triangle']))).unsqueeze(0) for _ in range(n)]
    def __len__(self): return len(self.data)
    def __getitem__(self, i): return self.data[i]

class ConvBlock(nn.Module):
    def __init__(self, in_ch, out_ch, t_dim):
        super().__init__()
        self.conv1, self.conv2 = nn.Conv2d(in_ch, out_ch, 3, padding=1), nn.Conv2d(out_ch, out_ch, 3, padding=1)
        self.t_mlp, self.bn1, self.bn2 = nn.Linear(t_dim, out_ch), nn.BatchNorm2d(out_ch), nn.BatchNorm2d(out_ch)
    def forward(self, x, t):
        h = F.relu(self.bn1(self.conv1(x))) + self.t_mlp(t)[:, :, None, None]
        return F.relu(self.bn2(self.conv2(h)))

class TinyUNet(nn.Module):
    def __init__(self, ch=32, t_dim=64):
        super().__init__()
        self.t_mlp = nn.Sequential(nn.Linear(1, t_dim), nn.ReLU(), nn.Linear(t_dim, t_dim))
        self.enc1, self.enc2 = ConvBlock(1, ch, t_dim), ConvBlock(ch, ch*2, t_dim)
        self.pool, self.bottleneck = nn.MaxPool2d(2), ConvBlock(ch*2, ch*2, t_dim)
        self.up2, self.dec2 = nn.ConvTranspose2d(ch*2, ch*2, 2, stride=2), ConvBlock(ch*4, ch, t_dim)
        self.up1, self.dec1 = nn.ConvTranspose2d(ch, ch, 2, stride=2), ConvBlock(ch*2, ch, t_dim)
        self.out = nn.Conv2d(ch, 1, 1)
    def forward(self, x, t):
        te = self.t_mlp(t.float().view(-1, 1))
        e1, e2 = self.enc1(x, te), self.enc2(self.pool(self.enc1(x, te)), te)
        b = self.bottleneck(self.pool(e2), te)
        d2, d1 = self.dec2(torch.cat([self.up2(b), e2], 1), te), None
        d1 = self.dec1(torch.cat([self.up1(d2), e1], 1), te)
        return self.out(d1)

class DDPM:
    def __init__(self, model, T=200, dev='cuda'):
        self.model, self.T, self.dev = model.to(dev), T, dev
        self.betas = torch.linspace(1e-4, 0.02, T).to(dev)
        self.alphas = 1. - self.betas
        self.abar = torch.cumprod(self.alphas, 0)
    def q_sample(self, x0, t, noise=None):
        noise = noise if noise is not None else torch.randn_like(x0)
        sa, s1a = torch.sqrt(self.abar[t])[:,None,None,None], torch.sqrt(1.-self.abar[t])[:,None,None,None]
        return sa * x0 + s1a * noise, noise
    def p_sample(self, x, t):
        tt = torch.full((x.size(0),), t, device=self.dev, dtype=torch.long)
        pn = self.model(x, tt)
        mean = (1./torch.sqrt(self.alphas[t])) * (x - (self.betas[t]/torch.sqrt(1.-self.abar[t])) * pn)
        return mean + torch.sqrt(self.betas[t]) * torch.randn_like(x) if t > 0 else mean
    @torch.no_grad()
    def sample(self, shape):
        self.model.eval()
        x = torch.randn(shape, device=self.dev)
        for t in reversed(range(self.T)): x = self.p_sample(x, t)
        return x.clamp(0, 1)

def save_grid(samples, path, nrow=4):
    from PIL import Image
    n, grid = samples.size(0), np.zeros(((samples.size(0)+nrow-1)//nrow * 28, nrow * 28), dtype=np.uint8)
    for i, s in enumerate(samples):
        r, c = i // nrow, i % nrow
        grid[r*28:(r+1)*28, c*28:(c+1)*28] = (s.squeeze().cpu().numpy() * 255).astype(np.uint8)
    Image.fromarray(grid).save(path); print(f"Saved: {path}")

def train(out_dir: str, mins: float = 5.0):
    dev = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f"Device: {dev}")
    loader = DataLoader(ShapesDataset(3000), batch_size=64, shuffle=True)
    model, ddpm = TinyUNet(), None
    ddpm = DDPM(model, T=200, dev=dev)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    start, max_s, epoch, loss_sum, step = time.time(), mins * 60, 0, 0, 0
    print(f"Training {mins} min...")
    while (time.time() - start) < max_s:
        epoch += 1
        for batch in loader:
            if (time.time() - start) >= max_s: break
            x0, t = batch.to(dev), torch.randint(0, ddpm.T, (batch.size(0),), device=dev)
            xn, noise = ddpm.q_sample(x0, t)
            loss = F.mse_loss(model(xn, t), noise)
            opt.zero_grad(); loss.backward(); opt.step()
            loss_sum += loss.item(); step += 1
            if step % 100 == 0: print(f"Step {step}, Ep {epoch}, Loss: {loss_sum/step:.4f}, Time: {time.time()-start:.1f}s")
    elapsed = time.time() - start
    print(f"Done: {step} steps, {epoch} ep, {elapsed:.1f}s, loss: {loss_sum/step:.4f}")
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    print("Generating 16 samples...")
    save_grid(ddpm.sample((16, 1, 28, 28)), out / "samples_grid.png")
    torch.save(model.state_dict(), out / "model.pt")
    with open(out / "training_info.json", "w") as f:
        json.dump({"steps": step, "epochs": epoch, "loss": loss_sum/step, "time_s": elapsed, "device": dev}, f, indent=2)
    return {"steps": step, "loss": loss_sum/step}

if __name__ == "__main__":
    train("/home/ubuntu/.openclaw/workspace/control-plane/artifacts/shapes_diffusion", 5.0)
