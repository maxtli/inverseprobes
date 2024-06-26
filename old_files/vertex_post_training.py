# %%
import torch
from tqdm import tqdm
from sys import argv
import torch.optim
import os
import argparse
from mask_samplers.MaskSampler import ConstantMaskSampler
from pruners.VertexPruner import VertexPruner
from utils.MaskConfig import VertexInferenceConfig
from utils.training_utils import load_model_data, LinePlot, load_args
from utils.circuit_utils import retrieve_mask, discretize_mask, get_ioi_nodes, nodes_to_vertex_mask
from utils.task_datasets import get_task_ds
# %%

model_name = "gpt2-small"
owt_batch_size = 10
device, model, tokenizer, owt_iter = load_model_data(model_name, owt_batch_size)
model.train()
# model.cfg.use_attn_result = True
n_layers = model.cfg.n_layers
n_heads = model.cfg.n_heads

# %%
# settings
args = load_args("pruning_vertices_auto", 1e-3)
folder, reg_lamb, dataset, tau = args["folder"], args["lamb"], args["dataset"], args["tau"]

batch_size=75
pruning_cfg = VertexInferenceConfig(model.cfg, device, folder, batch_size=batch_size)
pruning_cfg.lamb = reg_lamb
pruning_cfg.n_samples = 1

task_ds = get_task_ds(dataset, batch_size, device)

for param in model.parameters():
    param.requires_grad = False

# %%
mask_sampler = ConstantMaskSampler()
vertex_pruner = VertexPruner(model, pruning_cfg, task_ds.init_modes(), mask_sampler)
vertex_pruner.add_patching_hooks()

if folder.split("/")[-1] == "manual":
    ioi_nodes = get_ioi_nodes()
    discrete_mask = nodes_to_vertex_mask(ioi_nodes)
else:
    prune_mask, state_dict = retrieve_mask(folder, state_dict=True)
    if os.path.exists(f"{folder}/fit_nodes_{tau}.pth"):
        state_dict = torch.load(f"{folder}/fit_nodes_{tau}.pth")
        print(state_dict)
    vertex_pruner.load_state_dict(state_dict, strict=False)
    discrete_mask = discretize_mask(prune_mask, tau)
mask_sampler.set_mask(discrete_mask)

modal_optimizer = torch.optim.AdamW([vertex_pruner.modal_attention, vertex_pruner.modal_mlp], lr=pruning_cfg.lr_modes, weight_decay=0)

# %%

max_batches = 6000
for no_batches in tqdm(range(vertex_pruner.log.t, max_batches)):

    modal_optimizer.zero_grad()

    batch, last_token_pos = task_ds.next_batch(tokenizer)
    loss = vertex_pruner(batch, last_token_pos, timing=False)
    loss.backward()
    modal_optimizer.step()

    if no_batches % -100 == -1:
        torch.save({"modal_attention": vertex_pruner.modal_attention, "modal_mlp": vertex_pruner.modal_mlp}, f"{folder}/fit_modes_{tau}.pth")
        vertex_pruner.log.plot(["kl_loss"], mv=100, save=f"{folder}/fit_modes_{tau}.png")
# %%
