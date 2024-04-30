# %%
import torch
from transformer_lens import HookedTransformer
import numpy as np 
from tqdm import tqdm
from fancy_einsum import einsum
from einops import rearrange
import math
from functools import partial
import torch.optim
import time
import pandas as pd 
import seaborn as sns
import matplotlib.pyplot as plt
import pickle
from utils.training_utils import tuned_lens_hook, load_model_data, save_hook_last_token, LinePlot
from utils.lens_utils import apply_lens, apply_modal_lens
# %%

sns.set()
folder="results/modal_lens/linear_oca"
modal_lens_folder="results/modal_lens/random_init"
tuned_lens_folder = "results/tuned_lens"
# %%
# model_name = "EleutherAI/pythia-70m-deduped"
model_name = "gpt2-small"
batch_size = 200
clip_value = 1e5
device, model, tokenizer, owt_iter = load_model_data(model_name, batch_size)
shared_bias = False

n_layers = model.cfg.n_layers
n_heads = model.cfg.n_heads
head_dim = model.cfg.d_head
d_model = model.cfg.d_model
lr = 1e-2

# learning hyperparameters
kl_loss = torch.nn.KLDivLoss(reduction="none")

resid_points_filter = lambda layer_no, name: name == f"blocks.{layer_no}.hook_resid_pre"

# %%

lens_weights = [torch.nn.Parameter(torch.randn(d_model, d_model).to(device)) for _ in range(n_layers)]
lens_bias = [torch.nn.Parameter(torch.randn(d_model,).to(device)) for _ in range(n_layers)]
lens_optimizer = torch.optim.AdamW([*lens_weights, *lens_bias], lr=lr, weight_decay=1e-3)

# lens_optimizer = torch.optim.AdamW(attn_bias, lr=lr, weight_decay=0)
# lens_scheduler = torch.optim.lr_scheduler.StepLR(lens_optimizer, step_size=200, gamma=0.9)

for param in model.parameters():
    param.requires_grad = False

for p in lens_weights:
    p.register_hook(lambda grad: torch.nan_to_num(grad, nan=0, posinf=0, neginf=0))

for p in lens_bias:
    p.register_hook(lambda grad: torch.nan_to_num(grad, nan=0, posinf=0, neginf=0))

# %%
# with open(f"{tuned_lens_folder}/lens_weights.pkl", "rb") as f:
#     tuned_lens_weights = pickle.load(f)
# with open(f"{tuned_lens_folder}/lens_bias.pkl", "rb") as f:
#     tuned_lens_bias = pickle.load(f)
with open(f"{modal_lens_folder}/lens_weights.pkl", "rb") as f:
    attn_bias = pickle.load(f)

def get_oca_linear_lens_loss(batch):
    activation_storage = []

    model_probs = model.run_with_hooks(
            batch,
            fwd_hooks=[
                *[(partial(resid_points_filter, layer_no), 
                   partial(save_hook_last_token, activation_storage),
                    ) for layer_no in range(n_layers)],
                ]
    )[:,-1].softmax(dim=-1).unsqueeze(1)

    linear_lens_probs = apply_lens(model, lens_weights, lens_bias, activation_storage)
    modal_lens_probs = apply_modal_lens(model, attn_bias, activation_storage)

    kl_losses = kl_loss(linear_lens_probs.log(), modal_lens_probs).sum(dim=-1)

    with torch.no_grad():
        linear_lens_losses = kl_loss(linear_lens_probs.log(), model_probs).sum(dim=-1)
        modal_lens_losses = kl_loss(modal_lens_probs.log(), model_probs).sum(dim=-1)
    return kl_losses, linear_lens_losses, modal_lens_losses, activation_storage

# %%
# modal lens train
modal_loss_series = [f"modal_loss_{k}" for k in range(n_layers)]
linear_loss_series = [f"linear_loss_{k}" for k in range(n_layers)]
lm_loss_series = [f"lm_loss_{k}" for k in range(n_layers)]

lp = LinePlot([*lm_loss_series, *modal_loss_series, *linear_loss_series, 'step_size'])
    
for i in tqdm(range(50000)):
    batch = next(owt_iter)['tokens']
    lens_optimizer.zero_grad()

    kl_losses, linear_lens_losses, modal_lens_losses, _ = get_oca_linear_lens_loss(batch)
    kl_losses = kl_losses.mean(dim=0)
    loss = kl_losses.sum()

    loss.backward()

    linear_lens_losses = linear_lens_losses.mean(dim=0)
    modal_lens_losses = modal_lens_losses.mean(dim=0)
    prev_weights = torch.stack(lens_weights, dim=0).detach()

    lens_optimizer.step()

    step_sz = (torch.stack(lens_weights, dim=0)-prev_weights).abs().sum()
    lp.add_entry({
        "step_size": step_sz.item(), 
        **{f"lm_loss_{k}": kl_losses[k].item() for k in range(n_layers)},
        **{f"modal_loss_{k}": modal_lens_losses[k].item() for k in range(n_layers)},
        **{f"linear_loss_{k}": linear_lens_losses[k].item() for k in range(n_layers)},
    })

    if math.isnan(lp.stat_book["step_size"][-1]):
        break

    if i % -50 == -1:
        lp.plot(series=lm_loss_series, subplots=3, save=f"{folder}/train_modal.png", twinx=False, mv=20)
        # lp.plot(series=modal_loss_series, subplots=3, save=f"{folder}/train_modal.png", twinx=False, mv=20)
        lp.plot(series=linear_loss_series, subplots=3, save=f"{folder}/train_linear.png", twinx=False, mv=20)
        with open(f"{folder}/linear_lens_weights.pkl", "wb") as f:
            pickle.dump(lens_weights, f)
        with open(f"{folder}/linear_lens_bias.pkl", "wb") as f:
            pickle.dump(lens_bias, f)

# %%
