# %%

import torch
from transformer_lens import HookedTransformer
from itertools import cycle
import torch.optim
from fancy_einsum import einsum
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import os
import argparse
from utils.data import retrieve_owt_data


# %%

default_args = {
    "name": None,
    "lamb": 1e-3,
    "dataset": "ioi",
    "subfolder": None,
    "priorscale": None,
    "priorlamb": None
}

def load_args(run_type, default_lamb, defaults={}):
    my_args = {**default_args, **defaults, "lamb": default_lamb}
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument('-l', '--lamb',
                            help='regularization constant')
        parser.add_argument('-d', '--dataset',
                            help='ioi or gt')
        parser.add_argument('-s', '--subfolder',
                            help='where to load/save stuff')
        parser.add_argument('-t', '--priorscale',
                            help='prior strength')
        parser.add_argument('-p', '--priorlamb',
                            help='which vertex lambda')
        parser.add_argument('-n', '--name',
                            help='run name, e.g. edges or vertex prior')
        parser.add_argument('-t', '--tau',
                            help='threshold to use for post training')

        args = parser.parse_args()

        for k in args:
            my_args[k] = args[k]
            if k in {"lamb", "priorscale", "priorlamb", "tau"}:
                my_args[k] = float(my_args[k])
    except:
        pass

    print(my_args["lamb"])
    parent = "results"

    run_folder = my_args["dataset"] if my_args["name"] is None else f"{my_args['dataset']}_{my_args['name']}"
    if my_args["subfolder"] is not None:
        folder=f"{parent}/{run_type}/{run_folder}/{my_args['subfolder']}"
    elif my_args["priorlamb"] is not None:
        folder=f"{parent}/{run_type}/{run_folder}/{my_args['lamb']}-{my_args['priorlamb']}-{my_args['priorscale']}"
    else:
        folder=f"{parent}/{run_type}/{run_folder}/{my_args['lamb']}"

    if not os.path.exists(folder):
        os.makedirs(folder)
    
    my_args["folder"] = folder
    return my_args

def load_model_data(model_name, batch_size=8, ctx_length=25, repeats=True, ds_name=False, device="cuda:0"):
    # device="cpu"
    device = torch.device(device if torch.cuda.is_available() else "cpu")
    model = HookedTransformer.from_pretrained(model_name, device=device)
    tokenizer = model.tokenizer
    try:
        if ds_name:
            owt_loader = retrieve_owt_data(batch_size, ctx_length, tokenizer, ds_name=ds_name)
        else:
            owt_loader = retrieve_owt_data(batch_size, ctx_length, tokenizer)
        if repeats:
            owt_iter = cycle(owt_loader)
        else:
            owt_iter = owt_loader
    except:
        owt_iter = None
    return device, model, tokenizer, owt_iter

# %%

def save_hook_last_token(save_to, act, hook):
    save_to.append(act[:,-1,:])

def ablation_hook_last_token(batch_feature_idx, repl, act, hook):
    # print(act.shape, hook.name)
    # act[:,-1,:] = repl

    # act: batch_size x seq_len x activation_dim
    # repl: batch_size x features_per_batch x activation_dim
    # print(batch_feature_idx[:,0].dtype)
    # act = act.unsqueeze(1).repeat(1,features_per_batch,1,1)[batch_feature_idx[:,0],batch_feature_idx[:,1]]
    act = act[batch_feature_idx]
    # sns.histplot(torch.abs(act[:,-1]-repl).flatten().detach().cpu().numpy())
    # plt.show()
    act[:,-1] = repl
    # returns: (batch_size * features_per_batch) x seq_len x activation_dim
    # act = torch.cat([act,torch.zeros(1,act.shape[1],act.shape[2]).to(device)], dim=0)
    return act
    # return act.repeat(features_per_batch,1,1)
    # pass

def ablation_all_hook_last_token(repl, act, hook):
    # print(act.shape, hook.name)
    # act[:,-1,:] = repl

    # act: batch_size x seq_len x activation_dim
    # repl: batch_size x features_per_batch x activation_dim
    # print(batch_feature_idx[:,0].dtype)
    # act = act.unsqueeze(1).repeat(1,features_per_batch,1,1)[batch_feature_idx[:,0],batch_feature_idx[:,1]]
    # sns.histplot(torch.abs(act[:,-1]-repl).flatten().detach().cpu().numpy())
    # plt.show()
    act[:,-1] = repl
    # returns: (batch_size * features_per_batch) x seq_len x activation_dim
    # act = torch.cat([act,torch.zeros(1,act.shape[1],act.shape[2]).to(device)], dim=0)
    return act
    # return act.repeat(features_per_batch,1,1)
    # pass

def ablation_hook_copy_all_tokens(bsz, n_heads, act, hook):
    # need to repeat this N times for the number of heads.
    act = torch.cat([act,*[act[:bsz] for _ in range(n_heads)]], dim=0)
    return act

def ablation_hook_attention_all_tokens(constants, bsz, activation_storage, attentions, hook):
    n_heads = constants.shape[0]
    start = bsz * n_heads
    for i in range(constants.shape[0]):
        # if attentions.shape[0] > 400:
        # print(start)
        attentions[-start:-start+bsz,:,i] = constants[i].clone()
        start -= bsz
    
    # print(attentions.shape)
    # if attentions.shape[0] > 400:
    #     sns.histplot(attentions[:bsz][attentions[:bsz].abs() > 20].detach().flatten().cpu())
    #     print((attentions[:bsz].abs() > 500).nonzero())
    #     print(attentions[:bsz][(attentions[:bsz].abs() > 500)])
        
    # ignore first token because it is crazy
    with torch.no_grad():
        activation_storage.append(attentions[:bsz,1:].mean(dim=[0,1]))
    return attentions

# attentions: (batch_size + batch_size * n_samples) x seq_len x n_heads x d_model
# constants: n_heads x d_model
# prune mask: (batch_size * n_samples) x n_heads, 0 = prune, 1 = keep
def pruning_hook_attention_all_tokens(constants, prune_mask, bsz, attentions, hook):
    # N by 2. First column = batch item, second column = head idx
    prune_mask = prune_mask.unsqueeze(1).unsqueeze(-1)
    attentions[bsz:] = (1-prune_mask) * constants + prune_mask * attentions[bsz:].clone()

    # prune_idx = prune_mask.clone()
    # attentions[bsz + prune_idx[:,0],:,prune_idx[:,1]] = prune_idx * constants[prune_idx[:,1]]
    return attentions

def tuned_lens_hook(activation_storage, tuned_lens_weights, tuned_lens_bias, act, hook):
    activation_storage.append(einsum("result activation, batch activation -> batch result", tuned_lens_weights, act[:,-1]) + tuned_lens_bias)
    return act

# rec = number of items to record
# prev_means: rec x 1
# prev_vars: rec x 1
# batch_results: rec x n_samples
# no batches: number of batches represented in prev_means and prev_variances
# 
def update_means_variances(prev_means, prev_vars, batch_results, no_batches):
    # computing variance iteratively using a trick
    prev_vars = prev_vars * (batch_results.shape[-1] * no_batches - 1)
    vars = prev_vars + (batch_results - prev_means).square().sum(dim=-1, keepdim=True) - (batch_results.mean(dim=-1, keepdim=True) - prev_means).square()

    no_batches = no_batches + 1

    if batch_results.shape[-1] * no_batches > 1:
        vars = vars / (batch_results.shape[-1] * no_batches - 1)

    means = (no_batches * prev_means + batch_results.mean(dim=-1, keepdim=True)) / (no_batches + 1)

    return means, vars

# rec = number of items to record
# prev_means: rec x 1
# prev_vars: rec x 1
# batch_results: rec x n_samples
# n_batches_by_head: rec x 1
# n_samples_by_head: rec x 1
# batch_samples_by_head: rec x n_samples
def update_means_variances_mixed(prev_means, prev_vars, batch_results, n_batches_by_head, n_samples_by_head, batch_samples_by_head):
    # computing variance iteratively using a trick
    new_batches_by_head = n_batches_by_head + (batch_samples_by_head > 0).sum(dim=-1, keepdim=True)
    new_samples_by_head = n_samples_by_head + batch_samples_by_head.sum(dim=-1, keepdim=True)

    means = (n_samples_by_head * prev_means + (batch_samples_by_head * batch_results).sum(dim=-1, keepdim=True)) / new_samples_by_head

    prev_vars = prev_vars * (n_batches_by_head - 1)
    vars = prev_vars + (batch_samples_by_head * (batch_results - prev_means).square()).sum(dim=-1, keepdim=True) - new_samples_by_head * (means - prev_means).square()
    vars = torch.where(
        vars > 0,
        vars / (new_batches_by_head - 1),
        vars
    )
    
    return means, vars, new_batches_by_head, new_samples_by_head


# %%

# Unit test for update means variances mixed
# true_means = (torch.randn((100,1)) * 50).to(device)
# true_vars = (torch.randn((100,1)).abs() * 50).to(device)

# est_means = torch.zeros_like(true_means).to(device)
# est_vars = torch.zeros_like(true_means).to(device)
# n_batches_by_head = torch.zeros_like(true_means).to(device)
# n_samples_by_head = torch.zeros_like(true_means).to(device)

# for b in range(100):
#     mean_samples = []
#     sample_counts = []
#     for s in range(5):
#         n_samples = (torch.randint(0,10,(100,1)) - 5).relu().to(device)
#         idx_arr = torch.arange(10).unsqueeze(0).repeat(100,1).to(device)
#         idx_mask = (idx_arr < n_samples) * 1

#         batch_samples = true_vars.sqrt() * torch.randn((100,10)).to(device) + true_means
#         batch_means = torch.where(
#             n_samples < 1, 
#             0,
#             (batch_samples * idx_mask).sum(dim=-1, keepdim=True) / n_samples
#         )
#         mean_samples.append(batch_means)
#         sample_counts.append(n_samples)
#     mean_samples = torch.cat(mean_samples, dim=1) 
#     sample_counts = torch.cat(sample_counts, dim=1)

#     est_means, est_vars, n_batches_by_head, n_samples_by_head = update_means_variances_mixed(est_means, est_vars, mean_samples, n_batches_by_head, n_samples_by_head, sample_counts)

#     if b % -10 == -1:
#         sns.scatterplot(x=est_vars.flatten().cpu(), y=true_vars.flatten().cpu())
#         sns.lineplot(x=[0,200], y=[0,200])
#         plt.show()

#         sns.scatterplot(x=est_means.flatten().cpu(), y=true_means.flatten().cpu())
#         sns.lineplot(x=[-200,200], y=[-200,200])
#         plt.show()

class LinePlot:
    def __init__(self, stat_list, pref_start=100):
        self.stat_list = stat_list
        self.stat_book = {x: [] for x in stat_list}
        self.t = 0
        self.last_tick = 0
        self.early_term_count = 0
        self.pref_start = pref_start
    
    def add_entry(self, entry):
        for k in self.stat_book:
            if k in entry:
                self.stat_book[k].append(entry[k])
            # default behavior is flat line
            elif self.t == 0:
                self.stat_book[k].append(0)
            else:
                self.stat_book[k].append(self.stat_book[k][-1])
        self.t += 1
    
    def stat_sig_growth(self, series, avg_intv=10, comp_intv=200, start_t=0):
        if self.t - start_t <= comp_intv + avg_intv + 1:
            return False
        historical_avg = [np.mean(self.stat_book[series][-i-avg_intv-1:-i-1]) for i in range(comp_intv // 2, comp_intv, (avg_intv // 3))]
        rolling_avg = np.mean(self.stat_book[series][-avg_intv:])

        # decline, growth
        return 1 - rolling_avg / np.quantile(historical_avg, .1), rolling_avg / np.quantile(historical_avg, .9) - 1
        
    def plot(self, series=None, subplots=None, step=1, start=None, end=0, agg='mean', twinx=True, mv=False, save=None):
        if start is None:
            start = self.pref_start
        if series is None:
            series = self.stat_list
        if end <= start:
            end = self.t
            if end <= start:
                start = 0
        t = [i for i in range(start, end, step)]
        ax = None
        (h,l) = ([],[])
        colors = ["green", "blue", "red", "orange"]
        if subplots is not None:
            rows = (len(series)-1) // subplots + 1
            f, axes = plt.subplots(rows, subplots, figsize=(rows * 5, subplots * 5))
            
        for i,s in enumerate(series):
            if agg == 'mean':
                yvals = [np.mean(self.stat_book[s][i:i+step]) for i in range(start, end, step)]
            else:
                yvals = [self.stat_book[s][i] for i in range(start, end, step)]
            if twinx is True:
                params = {"x": t, "y": yvals, "label": s}
                if len(series) <= 4:
                    params["color"] = colors[i]
                if ax is None:
                    ax = sns.lineplot(**params)
                    h, l = ax.get_legend_handles_labels()
                    ax.get_legend().remove()
                    cur_ax = ax
                else:
                    ax2 = sns.lineplot(**params, ax=ax.twinx())
                    ax2.get_legend().remove()
                    h2, l2 = ax2.get_legend_handles_labels()
                    h += h2
                    l += l2
                    cur_ax = ax
            else:
                if subplots is not None:
                    ax = sns.lineplot(x=t, y=yvals, label=s, ax=axes[i // subplots, i % subplots])
                    cur_ax = ax
            if mv:
                mv_series = [np.mean(yvals[i:min(len(yvals),i+mv)]) for i in range(len(yvals))]
                sns.lineplot(x=t, y=mv_series, label=f"{s}_mv_{mv}", ax=cur_ax)
        if h is None:
            plt.legend()
        else:
            plt.legend(h, l)
        plt.tight_layout()

        if save:
            plt.savefig(save)
        plt.show()
        plt.close()

    def export():
        pass
