import os

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.optim import lr_scheduler
from torch.utils.data import DataLoader
from tqdm import tqdm

from dataset.image_dataset import ImageDataset
from dataset.transforms import get_train_transforms, get_val_transforms
from unet.model import UNet
from utils.constants import (
    N_EPOCHS,
    LEARNING_RATE,
    BATCH_SIZE,
    NUM_WORKERS,
    DEVICE,
    set_seed
)
from utils.metrics import BCEDiceLoss, hausdorff_distance, dice_score


def train_step(
        model: nn.Module,
        dataloader: DataLoader,
        optimizer: optim.Optimizer,
        criterion: callable,
        device: str,
        scaler: torch.amp.GradScaler,
        scheduler: lr_scheduler.LRScheduler = None
) -> dict[str, np.ndarray]:
    running_loss = 0.0
    running_dice = 0.0
    running_hausdorff = 0.0
    n_batches = len(dataloader)
    history = {"loss": np.zeros(n_batches), "dice": np.zeros(n_batches), "hausdorff": np.zeros(n_batches)}
    progress = tqdm(enumerate(dataloader), total=n_batches, desc="Training", unit="batch")

    model.train()
    for i, (imgs, masks) in progress:
        imgs, masks = imgs.to(device), masks.to(device)

        with torch.autocast(device):
            preds = model(imgs)
            loss = criterion(preds, masks)

        optimizer.zero_grad(set_to_none=True)
        scaler.scale(loss["combined"]).backward()
        scaler.step(optimizer)
        scaler.update()
        scheduler.step() if scheduler else None

        history["loss"][i] = loss["combined"].item()
        history["dice"][i] = dice_score(preds, masks)
        history["hausdorff"][i] = hausdorff_distance(preds, masks)

        running_loss += history["loss"][i]
        running_dice += history["dice"][i]
        running_hausdorff += history["hausdorff"][i]

        progress.set_postfix({
            "loss": f"{running_loss / (i + 1):.4f}",
            "dice": f"{running_dice / (i + 1):.4f}",
        })

    return history


def eval_step(model, dataloader, criterion, device, desc="Validating") -> dict[str, np.ndarray]:
    running_loss = 0.0
    running_dice = 0.0
    running_hausdorff = 0.0
    n_batches = len(dataloader)
    history = {"loss": np.zeros(n_batches), "dice": np.zeros(n_batches), "hausdorff": np.zeros(n_batches)}
    progress = tqdm(enumerate(dataloader), total=n_batches, desc=desc, unit="batch")

    model.eval()
    with torch.no_grad():
        for i, (imgs, masks) in progress:
            imgs, masks = imgs.to(device), masks.to(device)
            preds = model(imgs)
            loss = criterion(preds, masks)

            history["loss"][i] = loss["combined"].item()
            history["dice"][i] = dice_score(preds, masks)
            history["hausdorff"][i] = hausdorff_distance(preds, masks)

            running_loss += history["loss"][i]
            running_dice += history["dice"][i]
            running_hausdorff += history["hausdorff"][i]

            progress.set_postfix({
                "loss": f"{running_loss / (i + 1):.4f}",
                "dice": f"{running_dice / (i + 1):.4f}",
            })

    return history


def train():
    set_seed()
    print(f"Using device: {DEVICE}")

    train_set = ImageDataset("../data/train", transforms=get_train_transforms())
    val_set = ImageDataset("../data/val", transforms=get_val_transforms())
    test_set = ImageDataset("../data/test", transforms=get_val_transforms())

    train_loader = DataLoader(train_set, batch_size=BATCH_SIZE, shuffle=True, num_workers=NUM_WORKERS, pin_memory=True)
    val_loader = DataLoader(val_set, batch_size=BATCH_SIZE, shuffle=False, num_workers=NUM_WORKERS, pin_memory=True)
    test_loader = DataLoader(test_set, batch_size=BATCH_SIZE, shuffle=False, num_workers=NUM_WORKERS, pin_memory=True)

    model = UNet(3, 1).to(DEVICE)
    optimizer = optim.AdamW(model.parameters(), lr=LEARNING_RATE)
    scheduler = lr_scheduler.OneCycleLR(
        optimizer,
        max_lr=LEARNING_RATE,
        steps_per_epoch=len(train_loader),
        epochs=N_EPOCHS
    )
    criterion = BCEDiceLoss()
    scaler = torch.amp.GradScaler()

    for epoch in range(N_EPOCHS):
        print(f"\nEpoch [{epoch + 1}/{N_EPOCHS}]\n" + "-" * 30)

        train_hist = train_step(model, train_loader, optimizer, criterion, DEVICE, scaler, scheduler)
        val_hist = eval_step(model, val_loader, criterion, DEVICE)

        train_loss = train_hist["loss"].mean()
        train_dice = train_hist["dice"].mean()
        val_loss = val_hist["loss"].mean()
        val_dice = val_hist["dice"].mean()

        print(f"lr: {scheduler.get_last_lr()[0]:.6f}")
        print(f"Train Loss: {train_loss:.4f} - Train Dice: {train_dice:.4f}")
        print(f"Val Loss: {val_loss:.4f} - Val Dice: {val_dice:.4f}")
    print("-" * 30 + "\nTraining complete\n" + "-" * 30)

    test_hist = eval_step(model, test_loader, criterion, DEVICE, desc="Testing")
    test_loss = test_hist["loss"].mean()
    test_dice = test_hist["dice"].mean()
    print(f"Test Loss: {test_loss:.4f} - Test Dice: {test_dice:.4f}")

    os.makedirs("../models", exist_ok=True)
    torch.save(model.state_dict(), "../models/UNet.pth")


if __name__ == "__main__":
    train()
