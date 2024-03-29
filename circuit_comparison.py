
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
import glob
import json
import torch.optim
import os
import pandas as pd
import time
from itertools import cycle
import seaborn as sns
import matplotlib.pyplot as plt
import pickle
from circuit_utils import edges_to_mask, edge_prune_mask, vertex_prune_mask, retrieve_mask, discretize_mask, prune_dangling_edges, get_ioi_nodes, nodes_to_mask, nodes_to_vertex_mask, mask_to_nodes
from training_utils import LinePlot

# %%

folders=[
    {
        "edges": "pruning_edges_auto/ioi_clipped_edges", 
        "vertex": "pruning_vertices_auto/ioi_with_mlp", 
        "edges from vertex prior": "pruning_edges_auto/ioi_vertex_prior",
        # "reset_optim": "pruning_edges_auto/ioi_reinit",  
        # "prune_retrain": "pruning_edges_auto/ioi_reinit_lr",
        "iterative": "pruning_edges_auto/ioi_iter",
        "acdc": "acdc_ioi_runs",
        "manual": "pruning_vertices_auto/ioi_manual",
    },
    # ([], ["pruning_edges_auto/ioi_iter"]),
    # "pruning_edges_auto-2-24/ioi-2-26",
    # "pruning_edges_auto-2-24/gt",
    # "pruning_edges_auto-2-26/ioi_zero_init",
]

tau = -1
all_masks = {}
for task in folders:
    for k in task:
        folder = task[k]
        for lamb_path in glob.glob(f"{folder}/*"):
            lamb = lamb_path.split("/")[-1]
            print(lamb_path)
            try:
                float(lamb[-1])
                print(lamb[0])
                float(lamb[0])
                if k == "acdc":
                    prune_mask = torch.load(f"{folder}/edges_{lamb}.pth")
                    prune_mask = edges_to_mask(prune_mask)
                else:
                    prune_mask = retrieve_mask(lamb_path)
                    prune_mask = discretize_mask(prune_mask, tau)
            except:
                if lamb == "manual":
                    ioi_nodes = get_ioi_nodes()
                    prune_mask = nodes_to_mask(ioi_nodes)
                else:
                    continue
            if (k == "vertex"):
                print(prune_mask.keys())
                prune_mask = nodes_to_mask(mask_to_nodes(prune_mask, mask_type="nodes")[0], all_mlps=False)
                print(prune_mask.keys())

            print(prune_mask.keys())
            prune_mask, _, c_e, attn_ct, mlp_ct = prune_dangling_edges(prune_mask)
            print(lamb_path)
            all_masks[lamb_path] = (c_e, prune_mask, attn_ct, mlp_ct)

# %%
def get_mask_smiliarities(all_masks, output_folder):
    similarities = []
    node_similarities = []
    total_nodes = []

    for k in all_masks:
        similarities.append({"key1": k})
        node_similarities.append({"key1": k})
        edges_1, mask_1, attn_1, mlp_1 = all_masks[k]
        total_nodes_1 = (attn_1 > 0).sum().item() + (mlp_1 > 0).sum().item()
        total_nodes.append({"key":k, "nodes": total_nodes_1, "edges": edges_1})

        for ell in all_masks:
            edges_2, mask_2, attn_2, mlp_2 = all_masks[ell]

            similarity = np.sum([(m1 * mask_2[key][i] > 0).sum().item() for key in mask_1 for i, m1 in enumerate(mask_1[key])])

            similarities[-1][ell] = similarity / min(edges_1, edges_2)

            node_similarity = ((attn_1 > 0) * (attn_2 > 0)).sum().item() + ((mlp_1 > 0) * (mlp_2 > 0)).sum().item()
            
            node_similarities[-1][ell] = node_similarity / min(total_nodes_1, (attn_2 > 0).sum().item() + (mlp_2 > 0).sum().item())
   
    df = pd.DataFrame(similarities)
    df.to_csv(f"{output_folder}/edge_similarities.csv")

    node_similarities_df = pd.DataFrame(node_similarities)
    node_similarities_df.to_csv(f"{output_folder}/node_similarities.csv")

    tn_df = pd.DataFrame(total_nodes)
    tn_df.to_csv(f"{output_folder}/total_nodes.csv")

def get_similarities_manual(all_masks, output_folder):
    df = []
    ioi_edge_mask, _, c_e, attn_nodes, mlp_nodes = prune_dangling_edges(nodes_to_mask(get_ioi_nodes()))

    c_n = (attn_nodes > 0).sum().item() + (mlp_nodes > 0).sum().item()

    total_edges = np.sum([ts.nelement() for k in ioi_edge_mask for ts in ioi_edge_mask[k]])
    total_nodes = attn_nodes.nelement() + mlp_nodes.nelement()

    for k in all_masks:
        print(k)
        df.append({"key": k, "typ": k.split("/")[-2], "process": k.split("/")[0]})
        edges, mask, attn, mlp = all_masks[k]

        print(edges)

        similarity = np.sum([(m1 * ioi_edge_mask[key][i] > 0).sum().item() for key in mask for i, m1 in enumerate(mask[key])])

        print(similarity)

        df[-1]["shared_edges"] = similarity
        df[-1]["extra_edges"] = edges - similarity
        df[-1]["TPR_edges"] = similarity / c_e
        df[-1]["FPR_edges"] = (edges - similarity) / total_edges

        node_count = (attn > 0).sum().item() + (mlp > 0).sum().item()

        node_similarity = ((attn > 0) * (attn_nodes > 0)).sum().item() + ((mlp > 0) * (mlp_nodes > 0)).sum().item()

        df[-1]["shared_nodes"] = node_similarity
        df[-1]["extra_nodes"] = node_count - node_similarity
        df[-1]["TPR_nodes"] = node_similarity / c_n
        df[-1]["FPR_nodes"] = (node_count - node_similarity) / total_nodes

    df = pd.DataFrame(df)
    df.to_csv(f"{output_folder}/ROC.csv")
# %%

get_mask_smiliarities(all_masks, "similarities")
# %%

edge_df = pd.read_csv("similarities/edge_similarities.csv")
# %%
get_similarities_manual(all_masks, "similarities")
# %%
roc_df = pd.read_csv("similarities/ROC.csv")
# %%
with open("similarities/acdc_roc.json", "r") as f:
    acdc_roc_curve = json.load(f)
acdc_roc_curve = acdc_roc_curve['trained']['random_ablation']['ioi']['kl_div']['ACDC']
# %%
for task in folders:
    plt.figure(figsize=(4,20))
    sns.scatterplot(x=acdc_roc_curve["edge_fpr"], y=acdc_roc_curve["edge_tpr"], label="ACDC")

    for k in task:
        filt_df = roc_df[roc_df["typ"] == task[k].split("/")[-1]]
        sns.scatterplot(x=filt_df["FPR_edges"], y=filt_df["TPR_edges"], label=k)
    plt.xlim(0,0.2)
    plt.ylim(0,1)
# %%
for task in folders:
    plt.figure(figsize=(10,10))
    sns.scatterplot(x=acdc_roc_curve["node_fpr"], y=acdc_roc_curve["node_tpr"], label="ACDC")

    for k in task:
        filt_df = roc_df[roc_df["typ"] == task[k].split("/")[-1]]
        sns.scatterplot(x=filt_df["FPR_nodes"], y=filt_df["TPR_nodes"], label=k)
    plt.xlim(0,1)
    plt.ylim(0,1)
# %%
