#!/usr/bin/env python3
"""
Visualization for flow matching samples.
Generates comparison plots of generated vs true data distribution.
"""

import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
import json


def plot_samples(samples, true_data=None, title="Flow Matching Samples", output_path=None):
    """Plot generated samples vs true data."""
    fig, axes = plt.subplots(1, 2 if true_data is not None else 1, figsize=(12, 5))
    
    if true_data is not None:
        ax1, ax2 = axes
        ax1.scatter(true_data[:, 0], true_data[:, 1], alpha=0.5, s=5, c='blue')
        ax1.set_title("True Data Distribution")
        ax1.set_xlim(-3.5, 3.5)
        ax1.set_ylim(-3.5, 3.5)
        ax1.set_aspect('equal')
        ax1.grid(True, alpha=0.3)
        
        ax2.scatter(samples[:, 0], samples[:, 1], alpha=0.5, s=5, c='red')
        ax2.set_title("Generated Samples (Flow Matching)")
        ax2.set_xlim(-3.5, 3.5)
        ax2.set_ylim(-3.5, 3.5)
        ax2.set_aspect('equal')
        ax2.grid(True, alpha=0.3)
    else:
        axes.scatter(samples[:, 0], samples[:, 1], alpha=0.5, s=5, c='red')
        axes.set_title(title)
        axes.set_xlim(-3.5, 3.5)
        axes.set_ylim(-3.5, 3.5)
        axes.set_aspect('equal')
        axes.grid(True, alpha=0.3)
    
    plt.suptitle(title)
    plt.tight_layout()
    
    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"Saved plot to {output_path}")
    
    plt.close()


def plot_loss_curve(losses, output_path=None):
    """Plot training loss curve."""
    plt.figure(figsize=(10, 4))
    plt.plot(losses, alpha=0.7)
    plt.xlabel("Epoch")
    plt.ylabel("MSE Loss")
    plt.title("Flow Matching Training Loss")
    plt.grid(True, alpha=0.3)
    
    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"Saved loss curve to {output_path}")
    
    plt.close()


def create_true_data(n_samples=1000, n_modes=8, std=0.1):
    """Create reference 2D Gaussian mixture."""
    angles = np.linspace(0, 2 * np.pi, n_modes, endpoint=False)
    centers = np.stack([np.cos(angles), np.sin(angles)], axis=1) * 2.0
    mode_idx = np.random.randint(0, n_modes, n_samples)
    samples = centers[mode_idx] + np.random.randn(n_samples, 2) * std
    return samples


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", type=str, required=True, help="Results directory")
    args = parser.parse_args()
    
    results_dir = Path(args.results_dir)
    
    # Load samples
    samples = np.load(results_dir / "samples.npy")
    losses = np.load(results_dir / "losses.npy")
    
    # Create true data for comparison
    true_data = create_true_data(1000)
    
    # Plot
    plot_samples(samples, true_data, "Flow Matching: 2D Gaussian Mixture", 
                 results_dir / "samples_comparison.png")
    plot_loss_curve(losses, results_dir / "loss_curve.png")
    
    print("Visualization complete!")


if __name__ == "__main__":
    main()
