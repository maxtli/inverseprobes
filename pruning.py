# %%
import torch
import datasets
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
from itertools import cycle
from encoders import UntiedEncoder
import seaborn as sns
import matplotlib.pyplot as plt
import pickle
from training_utils import load_model_data, pruning_hook_attention_all_tokens, LinePlot

# %%

# model_name = "EleutherAI/pythia-70m-deduped"
model_name = "gpt2-small"
batch_size = 10
device, model, tokenizer, owt_iter = load_model_data(model_name, batch_size)
model.train()
model.cfg.use_attn_result = True

ioi_ds = datasets.load_from_disk("../plausibleablation/data/ioi/ioi")
ioi_loader = DataLoader(ioi_ds['train'], batch_size=batch_size, shuffle=True, pin_memory=True)
ioi_iter = cycle(iter(ioi_loader))

# %%
# inverse probe setting

n_layers = model.cfg.n_layers
n_heads = model.cfg.n_heads
lr = 1e-4
lamb = 1

# # learning hyperparameters
# convergence_tol = 1e-4
# similarity_tol = .05
# lr_act = 1e-4
# lr_feat = 1e-5
# updates_per_batch = 100
# relu = torch.nn.ReLU()
kl_loss = torch.nn.KLDivLoss(reduction="none")

# %%

# import modal values

with open("pruning/modes/modes_0.pkl", "rb") as f:
    # n_layers x n_heads x d_model
    modal_values = pickle.load(f)

# %%

# resid_points_filter = lambda layer_no, name: name == f"blocks.{layer_no}.hook_resid_pre"
attention_points_filter = lambda layer_no, name: name == f"blocks.{layer_no}.attn.hook_result"

# %%

# sample pruned heads independently from batch, or use same pruned heads for each batch item?
# currently using the former

# %%

# n_heads x 2, first column = location (alpha), second column = scale (beta)
n_samples = 25

# as in the louizos paper
starting_beta = 2/3
hard_concrete_endpoints = (-0.1, 1.1)
sampling_params = [torch.nn.Parameter(
    torch.stack(
        [torch.rand(n_heads,).log(), torch.ones(n_heads,) * starting_beta],
        dim=1
    ).to(device)
) for _ in range(n_layers)]
sampling_optimizer = torch.optim.SGD(sampling_params, lr=lr, weight_decay=0)

# %%

# beta and alpha should be same shape as x, or broadcastable
# def f_concrete(x, beta, alpha):
#     return ((x.log() - (1-x).log()) * beta - alpha.log()).sigmoid()

def sample_mask(unif, sampling_params):
    sampling_params = sampling_params.unsqueeze(1)

    # back prop against log alpha
    concrete = ((unif.log() - (1-unif).log() + sampling_params[:,:,:,0])/sampling_params[:,:,:,1]).sigmoid()

    hard_concrete = ((concrete + hard_concrete_endpoints[0]) * (hard_concrete_endpoints[1] - hard_concrete_endpoints[0])).clamp(0,1)

    # n_layers x (total_samples = batch_size * n_samples) x n_heads
    return hard_concrete

# %%

for param in model.parameters():
    param.requires_grad = False


# %%
# cum_prune = []
# for j in range(10):
#     all_sampling_params = torch.stack(sampling_params, dim=0)
#     unif = torch.rand((n_layers, batch_size * n_samples, n_heads))
#     prune_mask = sample_mask(unif, all_sampling_params)
#     cum_prune.append(prune_mask)

# cum_prune = torch.stack(cum_prune, dim=0).flatten()
# sns.histplot(cum_prune.detach())

# %%

# %%
lp = LinePlot(['step_size', 'kl_loss', 'av_alpha', 'complexity_loss'])
torch.autograd.set_detect_anomaly(True)

i = 0
while i < 1000:
    batch = next(owt_iter)['tokens'].to(device)

    b = next(ioi_iter)
    batch = tokenizer(b['ioi_sentences'], padding=True, return_tensors='pt')['input_ids'].to(device)
    last_token_pos = ((batch != tokenizer.pad_token_id) * torch.arange(batch.shape[1]).to(device)).argmax(dim=-1) - 1

    # if find_last_token:
    #     # full sequence includes the IO
    # else:
    #     last_token_pos = -1 * torch.ones(batch.shape[0]).to(device)


    sampling_optimizer.zero_grad()

    # sample
    all_sampling_params = torch.stack(sampling_params, dim=0)
    unif = torch.rand((n_layers, batch_size * n_samples, n_heads)).to(device)
    prune_mask = sample_mask(unif, all_sampling_params)

    model_results = model.run_with_hooks(
        # first batch_size samples are targets
            batch.repeat(n_samples + 1,1),
            fwd_hooks=[
                (partial(attention_points_filter, layer_no), 
                   partial(pruning_hook_attention_all_tokens,
                           modal_values[layer_no],
                           prune_mask[layer_no],
                           batch_size)
                ) for layer_no in range(n_layers)
            ]
    )
    model_results = model_results[torch.arange(model_results.shape[0]),last_token_pos.repeat(n_samples + 1)].softmax(dim=-1)

    # batch_size x vocab_size
    target_results = model_results[:batch_size]

    # n_samples x batch_size x vocab_size
    ablated_results = model_results[batch_size:].unflatten(0, (n_samples,batch_size))

    kl_losses = kl_loss(ablated_results.log(), target_results).sum(dim=-1)

    # alphas already logged
    complexity_loss = (all_sampling_params[:,0]-all_sampling_params[:,1] * (math.log(-hard_concrete_endpoints[0]/hard_concrete_endpoints[1]))).sigmoid()

    loss = kl_losses.sum() + lamb * complexity_loss.sum()

    loss.backward()

    prev_alphas = all_sampling_params[:,:,0].detach()
    prev_betas = all_sampling_params[:,:,1].detach()
    sampling_optimizer.step()

    nancount = torch.stack(sampling_params, dim=0).isnan().sum()
    print(nancount)
    
    if nancount > 0:
        for param in sampling_params:
            param[param[:,1].isnan().nonzero()[:,0],1] = 2/3

    nancount = torch.stack(sampling_params, dim=0).isnan().sum()
    print(nancount)
    if nancount > 0:
        break
    
    step_sz = (torch.stack(sampling_params, dim=0)[:,:,0] - prev_alphas).abs().sum()

    lp.add_entry({"step_size": step_sz.item(), "kl_loss": kl_losses.sum().item(), "av_alpha": all_sampling_params[:,:,0].mean().item(), "complexity_loss": complexity_loss.sum().item()})

    if i % 50 == 0:
        sns.histplot(prune_mask.detach().flatten().cpu())
        plt.show()

        sns.histplot(kl_losses.detach().flatten().cpu())
        plt.show()

        if i > 0:
            lp.plot()
    
    print("KL:", kl_losses.sum())
    print("Complexity:", complexity_loss.sum())

    i += 1

    



# %%
