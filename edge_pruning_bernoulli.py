# %%
import torch
import datasets
import os
from sys import argv
from torch.utils.data import DataLoader
from transformer_lens import HookedTransformer
import numpy as np 
from tqdm import tqdm
from fancy_einsum import einsum
from einops import rearrange
import math
from functools import partial
import torch.optim
import time
import argparse
from itertools import cycle
import seaborn as sns
import matplotlib.pyplot as plt
from circuit_utils import retrieve_mask
import pickle
from EdgePruner import EdgePruner
from MaskSampler import EdgeMaskBernoulliSampler
from MaskConfig import EdgeInferenceConfig
from task_datasets import IOIConfig, GTConfig
from training_utils import load_model_data, LinePlot

# %%
# load model
# model_name = "EleutherAI/pythia-70m-deduped"
model_name = "gpt2-small"
owt_batch_size = 10
device, model, tokenizer, owt_iter = load_model_data(model_name, owt_batch_size)
model.train()
model.cfg.use_split_qkv_input = True
model.cfg.use_hook_mlp_in = True
n_layers = model.cfg.n_layers
n_heads = model.cfg.n_heads

# %%
try:
    parser = argparse.ArgumentParser()
    parser.add_argument('-l', '--lamb',
                        help='regularization constant')
    parser.add_argument('-s', '--subfolder',
                        help='where to save stuff')
    args = parser.parse_args()
    reg_lamb = float(args.lamb)
    subfolder = args.subfolder
except:
    reg_lamb = None
    subfolder = None

if reg_lamb is None:
    reg_lamb = 3e-4

node_reg=5e-4
gpu_requeue = True
# reset_optim = 1000

print(reg_lamb)

if subfolder is not None:
    folder=f"pruning_edges_auto/ioi_edges_IS/{subfolder}"
else:
    folder=f"pruning_edges_auto/ioi_edges_IS/{reg_lamb}"

pretrained_folder = None
# f"pruning_edges_auto/ioi/300.0"
if not os.path.exists(folder):
    os.makedirs(folder)

pruning_cfg = EdgeInferenceConfig(model.cfg, device, folder, init_param=0)
pruning_cfg.lamb = reg_lamb
pruning_cfg.batch_size = 1
pruning_cfg.n_samples = 90
pruning_cfg.initialize_params_probs(1)

task_ds = IOIConfig(pruning_cfg.batch_size, device)

for param in model.parameters():
    param.requires_grad = False

# %%

# prune_retrain = True
# prune_length = 200
# retrain_length = 100

# %%
mask_sampler = EdgeMaskBernoulliSampler(pruning_cfg, node_reg=node_reg)
edge_pruner = EdgePruner(model, pruning_cfg, task_ds.init_modes(), mask_sampler)
edge_pruner.add_cache_hooks()
edge_pruner.add_patching_hooks()

sampling_optimizer = torch.optim.AdamW(mask_sampler.parameters(), lr=pruning_cfg.lr, weight_decay=0)
modal_optimizer = torch.optim.AdamW([edge_pruner.modal_attention, edge_pruner.modal_mlp], lr=pruning_cfg.lr_modes, weight_decay=0)

# %%

lp_count = pruning_cfg.load_snapshot(edge_pruner, sampling_optimizer, modal_optimizer, gpu_requeue, pretrained_folder=None)

take_snapshot = partial(pruning_cfg.take_snapshot, edge_pruner, lp_count, sampling_optimizer, modal_optimizer)

example_modes = torch.load("pruning_edges_auto/ioi_edges_unif/0.0001/fit_modes_0.0.pth")
edge_pruner.load_state_dict({"modal_attention": example_modes['modal_attention'], "modal_mlp": example_modes['modal_mlp']}, strict=False)
# %%
# if prune_retrain and edge_pruner.log.t == 0:
#     edge_pruner.log.mode = "prune"
#     edge_pruner.log.cur_counter = 0

max_batches = 200

is_losses = []
prune_masks = []

mask_sampler.sample_mask()
mask_sampler.fix_mask = True

# moving_avg_prob = 0

for no_batches in tqdm(range(edge_pruner.log.t, max_batches)):

    plotting = no_batches % (-1 * pruning_cfg.record_every) == -1
    checkpointing = no_batches % (-1 * pruning_cfg.checkpoint_every * pruning_cfg.record_every) == -1

    batch, last_token_pos = task_ds.next_batch(tokenizer)
    last_token_pos = last_token_pos.int()

    # modal_optimizer.zero_grad()
    # sampling_optimizer.zero_grad()

    with torch.no_grad():

        # sample prune mask
        graph_suffix = f"-{no_batches}" if checkpointing else "" if plotting else None
        loss, mask_loss = edge_pruner(batch, last_token_pos, graph_suffix, separate_loss=True)

        log_probs = mask_sampler.compute_log_probs(mask_sampler.sampled_mask)

        # moving_avg_window = min(no_batches, 99)
        # moving_avg_prob = (log_probs.mean() + moving_avg_window * moving_avg_prob) / (moving_avg_window+1)

        is_loss = ((log_probs - log_probs.detach()).exp() * (loss))
        # .mean()

        is_losses.append(is_loss)
        prune_masks.append(mask_sampler.sampled_mask)


    # is_loss.backward()

    # prev_alphas = mask_sampler.get_sampling_params()[:,0].detach().clone()
    # prev_modes = edge_pruner.get_modes().detach().clone()

    # sampling_optimizer.step()
    # modal_optimizer.step()

    # mask_sampler.fix_nans()

    # with torch.no_grad():
    #     step_sz = (mask_sampler.get_sampling_params()[:,0] - prev_alphas).abs()
    #     step_sz = (step_sz - 1e-3).relu().sum() / (step_sz > 1e-3).sum()
    #     mode_step_sz = (edge_pruner.get_modes().clone() - prev_modes).norm(dim=-1).mean()
    #     lp_count.add_entry({
    #         "step_size": step_sz.item(), 
    #         "mode_step_size": mode_step_sz.item()
    #     })

    # if plotting:
    #     take_snapshot("")

    #     sns.histplot(x=log_probs.detach().cpu().flatten())
    #     plt.savefig(f"{folder}/log_probs.png")
    #     plt.close()

    #     if checkpointing:
    #         take_snapshot(f"-{no_batches}")
    
    # if reset_optim is not None and no_batches % (-1 * reset_optim) == -1:
    #     modal_optimizer = torch.optim.AdamW([edge_pruner.modal_attention, edge_pruner.modal_mlp], lr=pruning_cfg.lr_modes, weight_decay=0)
# %%
is_losses = torch.stack(is_losses, dim=0)
# %%
batch_masks = []
for batch_mask in tqdm(prune_masks):
    ts_batch_mask = torch.cat([torch.cat([ts.flatten(start_dim=1,end_dim=-1) for ts in batch_mask[k]], dim=-1) for k in batch_mask], dim=-1)
    batch_masks.append(ts_batch_mask)
batch_masks = torch.stack(batch_masks, dim=0)

# %%

folder="bernoulli/1"

 
# %%

def eves_law(is_losses, variable, ts_dim):

    sns.histplot(is_losses.var(dim=ts_dim).cpu().detach())
    plt.title(f"var(L | {variable})")
    plt.savefig(f"{folder}/var{variable}.png")
    plt.show()
    plt.close()

    sns.histplot(is_losses.mean(dim=ts_dim).cpu().detach())
    plt.savefig(f"{folder}/exp{variable}.png")
    plt.show()
    plt.close()

    print("Total variance", is_losses.flatten().var())
    print(f"Expectation of variance given {variable}", is_losses.var(dim=ts_dim).mean().item())
    print(f"Variance of expectation given {variable}", is_losses.mean(dim=ts_dim).var().item())


# %%
sns.histplot(is_losses.flatten().cpu().detach())
plt.title("All losses")
plt.savefig(f"{folder}/all_losses.png")
plt.show()
plt.close()

eves_law(is_losses, "X", -1)
eves_law(is_losses, "S", 0)
# %%

all_masks = batch_masks[0]
# %%

sns.histplot((1-all_masks).sum(dim=0).cpu())
plt.savefig(f"{folder}/excl_count.png")
plt.close()

# %%
excl_edge_loss = ((is_losses.mean(dim=0).unsqueeze(-1) * (1-all_masks)).sum(dim=0) / ((1-all_masks).sum(dim=0)))
excl_edge_var = ((is_losses.var(dim=0).unsqueeze(-1) * (1-all_masks)).sum(dim=0) / ((1-all_masks).sum(dim=0)))
sns.scatterplot(x=excl_edge_loss.cpu(), y=excl_edge_var.cpu(), s=1)
plt.xlabel("mean given exclusion")
plt.ylabel("var given exclusion")
plt.savefig(f"{folder}/excl.png")
plt.show()
plt.close()

# %%

m = retrieve_mask("pruning_edges_auto/ioi_vertex_prior/0.0001-0.005-0.3")
final_mask_vtx = torch.cat([torch.cat([ts.flatten() for ts in m[k]], dim=-1) for k in batch_mask], dim=-1)
sns.scatterplot(x=excl_edge_loss.cpu(), y=final_mask_vtx.cpu(), s=1)
plt.xlabel("mean given exclusion")
plt.ylabel("final vtx prior")
plt.savefig(f"{folder}/excl_comp_vtx.png")
plt.show()
plt.close()

# %%

m = retrieve_mask("pruning_edges_auto/ioi_edges_unif/0.0001")
final_mask_unif = torch.cat([torch.cat([ts.flatten() for ts in m[k]], dim=-1) for k in batch_mask], dim=-1)
sns.scatterplot(x=excl_edge_loss.cpu(), y=final_mask_unif.cpu(), s=1)
plt.xlabel("mean given exclusion")
plt.ylabel("final unif")
plt.savefig(f"{folder}/excl_comp_unif.png")
plt.show()
plt.close()

# %%
sns.scatterplot(x=final_mask_vtx.cpu(), y=final_mask_unif.cpu(), s=1)
plt.xlabel("final vtx prior")
plt.ylabel("final unif")
plt.savefig(f"{folder}/excl_comp.png")
plt.show()
plt.close()



# %%

# corr with mask size
sns.scatterplot(x=is_losses.flatten().cpu(), y=all_masks.sum(dim=-1).repeat(is_losses.shape[0]).cpu())

all_masks.shape

# %%