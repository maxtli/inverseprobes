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
import seaborn as sns
import matplotlib.pyplot as plt
import pickle
from training_utils import load_model_data, LinePlot
from utils.MaskConfig import IOIConfig

# %%

# model_name = "EleutherAI/pythia-70m-deduped"
model_name = "gpt2-small"
folder = "pruning_baseline/ioi_manual"
batch_size = 20
device, model, tokenizer, owt_iter = load_model_data(model_name, batch_size)
model.train()
model.cfg.use_attn_result = True

pruning_cfg = IOIConfig(model.cfg, device, folder, batch_size=batch_size)
# %%
# inverse probe setting

n_layers = model.cfg.n_layers
n_heads = model.cfg.n_heads
lr = 1e-2
lr_modes = 1e-3
lamb = 3

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

# with open("pruning/modes/modes_0.pkl", "rb") as f:
#     # n_layers x n_heads x d_model
#     modal_values = pickle.load(f)
with open("pruning_baseline/ioi_manual/modes_3.pkl", "rb") as f:
#     # n_layers x n_heads x d_model
    init_modes = pickle.load(f)

# %%

# resid_points_filter = lambda layer_no, name: name == f"blocks.{layer_no}.hook_resid_pre"
attention_points_filter = lambda layer_no, name: name == f"blocks.{layer_no}.attn.hook_result"

# %%

# n_heads x 2, first column = location (alpha), second column = scale (beta)
n_samples = 1

# # as in the louizos paper
# starting_beta = 2/3
# hard_concrete_endpoints = (-0.1, 1.1)

# # init_params = [torch.stack([torch.ones(n_heads,) * -2, torch.ones(n_heads,) * starting_beta], dim=1).to(device) for _ in range(n_layers)]

# # last modes

MANUAL_CIRCUIT = {
    "name mover": [
        (9, 9),  # by importance
        (10, 0),
        (9, 6),
    ],
    "backup name mover": [
        (10, 10),
        (10, 6),
        (10, 2),
        (10, 1),
        (11, 2),
        (9, 7),
        (9, 0),
        (11, 9),
    ],
    "negative": [(10, 7), (11, 10)],
    "s2 inhibition": [(7, 3), (7, 9), (8, 6), (8, 10)],
    "induction": [(5, 5), (5, 8), (5, 9), (6, 9)],
    "duplicate token": [
        (0, 1),
        (0, 10),
        (3, 0),
        # (7, 1),
    ],  # unclear exactly what (7,1) does
    "previous token": [
        (2, 2),
        (2, 9),
        # (4, 11),
        # (4, 3),
        # (4, 7),
        # (5, 6),
        # (3, 3),
        # (3, 7),
        # (3, 6),
    ],
}

attention_head_mask = [torch.zeros(n_heads,) for _ in range(n_layers)]

for type in MANUAL_CIRCUIT:
    for layer, head in MANUAL_CIRCUIT[type]:
        attention_head_mask[layer][head] = 1

prune_mask = [m.repeat(batch_size,1).to(device) for m in attention_head_mask]

# sampling_params = [torch.nn.Parameter(init_params[i]) for i in range(n_layers)]
# sampling_optimizer = torch.optim.Adam(sampling_params, lr=lr, weight_decay=0)

modal_values = [torch.nn.Parameter(init_modes[i]) for i in range(n_layers)]
modal_optimizer = torch.optim.AdamW(modal_values, lr=lr_modes, weight_decay=0)

# %%

# beta and alpha should be same shape as x, or broadcastable
# def f_concrete(x, beta, alpha):
#     return ((x.log() - (1-x).log()) * beta - alpha.log()).sigmoid()

# def sample_mask(unif, sampling_params):
#     sampling_params = sampling_params.unsqueeze(1)

#     # back prop against log alpha
#     concrete = (((.001+unif).log() - (1-unif).log() + sampling_params[:,:,:,0])/(sampling_params[:,:,:,1].relu()+.001)).sigmoid()

#     hard_concrete = ((concrete + hard_concrete_endpoints[0]) * (hard_concrete_endpoints[1] - hard_concrete_endpoints[0])).clamp(0,1)

#     # n_layers x (total_samples = batch_size * n_samples) x n_heads
#     return hard_concrete

# def temp_scheduler(k):
#     init = 1/10
#     return min(max(k-2000,0) / 20000,1) * init
# attentions: (batch_size + batch_size * n_samples) x seq_len x n_heads x d_model
# constants: n_heads x d_model
# prune mask: (batch_size * n_samples) x n_heads, 0 = prune, 1 = keep
def pruning_hook_attention_all_tokens(constants, prune_mask, bsz, attentions, hook):
    prune_mask = prune_mask.unsqueeze(1).unsqueeze(-1)
    attentions[bsz:] = (1-prune_mask) * constants + prune_mask * attentions[bsz:].clone()

    # prune_idx = prune_mask.clone()
    # attentions[bsz + prune_idx[:,0],:,prune_idx[:,1]] = prune_idx * constants[prune_idx[:,1]]
    return attentions


# %%

for param in model.parameters():
    param.requires_grad = False

# %%
    # inference
n_samples = 1
pruning_cfg = IOIConfig(model.cfg, device, folder)
losses = []
ds_test = pruning_cfg.get_test_set(tokenizer)
with torch.no_grad():
    for b in iter(ds_test):
        batch, last_token_pos = pruning_cfg.next_batch(tokenizer,b)
        batch = batch[:batch_size]
        last_token_pos = last_token_pos[:batch_size]
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
        # io token
        model_results = model_results[torch.arange(model_results.shape[0]),last_token_pos.repeat(n_samples + 1)]

        # io logits
        # model_results = model_results[torch.arange(model_results.shape[0]), batch[torch.arange(batch.shape[0]), last_token_pos+1].repeat(n_samples + 1)]

        # kl div
        model_results = model_results.log_softmax(dim=-1)

        # batch_size x vocab_size
        target_results = model_results[:batch_size]

        # n_samples x batch_size x vocab_size
        ablated_results = model_results[batch_size:].unflatten(0, (n_samples,batch_size))
        model_results = model_results.log_softmax(dim=-1)

        # batch_size x vocab_size
        target_results = model_results[:batch_size]

        # n_samples x batch_size x vocab_size
        ablated_results = model_results[batch_size:].unflatten(0, (n_samples,batch_size))

        kl_losses = kl_loss(ablated_results, target_results.exp()).sum(dim=-1)
        losses.append(kl_losses.mean().item())
        if len(losses) >= 20:
            break

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
lp = LinePlot(['kl_loss', 'mode_step_size'])
# lp_2 = LinePlot(['av_alpha', 'complexity_loss', 'temp_loss'])
torch.autograd.set_detect_anomaly(True)

i = 0
j = 0
while i < 100000:
    # batch = next(owt_iter)['tokens'].to(device)

    # b = next(ioi_iter)
    # batch = tokenizer(b['ioi_sentences'], padding=True, return_tensors='pt')['input_ids'].to(device)
    # last_token_pos = ((batch != tokenizer.pad_token_id) * torch.arange(batch.shape[1]).to(device)).argmax(dim=-1) - 1

    batch, last_token_pos = pruning_cfg.next_batch(tokenizer)
    modal_optimizer.zero_grad()
    # sampling_optimizer.zero_grad()

    # sample
    # all_sampling_params = torch.stack(sampling_params, dim=0)
    # unif = torch.rand((n_layers, batch_size * n_samples, n_heads)).to(device)
    # prune_mask = sample_mask(unif, all_sampling_params)

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
    # io token
    model_results = model_results[torch.arange(model_results.shape[0]),last_token_pos.repeat(n_samples + 1)]

    # io logits
    # model_results = model_results[torch.arange(model_results.shape[0]), batch[torch.arange(batch.shape[0]), last_token_pos+1].repeat(n_samples + 1)]

    # kl div
    model_results = model_results.log_softmax(dim=-1)

    # batch_size x vocab_size
    target_results = model_results[:batch_size]

    # n_samples x batch_size x vocab_size
    ablated_results = model_results[batch_size:].unflatten(0, (n_samples,batch_size))

    kl_losses = kl_loss(ablated_results, target_results.exp()).sum(dim=-1)
    # io_loss = target_results - ablated_results

    # alphas already logged
    # complexity_loss = (all_sampling_params[:,:,0]-all_sampling_params[:,:,1].relu() * (math.log(-hard_concrete_endpoints[0]/hard_concrete_endpoints[1]))).sigmoid()
    # temperature_loss = all_sampling_params[:,:,1].square().sum()

    loss = kl_losses.sum() 
    # + lamb * complexity_loss.sum() + temp_scheduler(i) * temperature_loss
    # loss = io_loss.mean() + lamb * complexity_loss.sum()

    loss.backward()

    # prev_alphas = all_sampling_params[:,:,0].detach()
    # prev_betas = all_sampling_params[:,:,1].detach()
    prev_modes = torch.stack(modal_values, dim=0).detach().clone()

    # sampling_optimizer.step()
    modal_optimizer.step()

    # nancount = torch.stack(sampling_params, dim=0).isnan().sum()
    
    # if nancount > 0:
    #     print("NANs", nancount)
    #     for param in sampling_params:
    #         param[param[:,1].isnan().nonzero()[:,0],1] = 2/3

    # nancount = torch.stack(sampling_params, dim=0).isnan().sum()
    # if nancount > 0:
    #     print("NANs", nancount)
    #     break
    
    # step_sz = (torch.stack(sampling_params, dim=0)[:,:,0] - prev_alphas).abs().sum()
    mode_step_sz = (torch.stack(modal_values, dim=0) - prev_modes).norm(dim=-1).mean()

    lp.add_entry({"kl_loss": kl_losses.mean().item(), "mode_step_size": mode_step_sz.item()})
    # lp_2.add_entry({"complexity_loss": complexity_loss.sum().item(),
    # "av_alpha": all_sampling_params[:,:,0].mean().item(), 
    # "temp_loss": all_sampling_params[:,:,1].relu().sum().item()})

    if i % 1000 == 10:
        # sns.histplot(prune_mask.detach().flatten().cpu())
        # plt.savefig(f"{folder}/mask_{j}.png")
        # plt.close()

        # sns.scatterplot(x=all_sampling_params[:,:,0].detach().flatten().cpu(), y=all_sampling_params[:,:,1].detach().flatten().cpu())
        # plt.savefig(f"{folder}/params_{j}.png")
        # plt.close()

        sns.histplot(kl_losses.detach().flatten().cpu())
        plt.savefig(f"{folder}/io_loss_{j}.png")
        plt.close()

        if i > 0:
            lp.plot(save=f"{folder}/train_{j}.png")
            # lp_2.plot(save=f"{folder}/train_reg_{j}.png")

        with open(f"{folder}/modes_{j}.pkl", "wb") as f:
            pickle.dump(modal_values, f)

        j += 1
    
    print("KL:", kl_losses.mean())
    # print("Complexity:", complexity_loss.sum())
    # print("Temp", temperature_loss.sum())
    i += 1

# %%
    


# %%

# 