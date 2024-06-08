# %%
import torch
import numpy as np 
import math
import os
import seaborn as sns
import matplotlib.pyplot as plt
import pickle
from utils.training_utils import plot_no_outliers

# %%
sns.set()

folder="results/ablation_loss"
plot_folder="plots_export/ablation_loss"
dataset_list = {"gt": "Greater-Than", "ioi": "IOI"}
ablation_types = ["zero", "mean", "resample", "refmean", "oca", "cf"]
ax_labels = {
    "zero": "Zero",
    "mean": "Mean", 
    "resample": "Resample",
    "refmean": "CF-Mean",
    "oca": "Optimal",
    "cf": "CF"
}

if not os.path.exists(plot_folder):
    os.makedirs(plot_folder)

ablation_data = {}
for ds in dataset_list:
    ablation_data[ds] = {}
    for ablation_type in ablation_types:
        ablation_data[ds][ablation_type] = torch.load(f"{folder}/{ds}/{ablation_type}_results.pth")

# %%

CORR_SIZE = 32
SMALL_SIZE = 12
MEDIUM_SIZE = 32
BIGGER_SIZE = 48

plt.rc('font', size=CORR_SIZE)          # controls default text sizes
plt.rc('axes', titlesize=SMALL_SIZE)     # fontsize of the axes title
plt.rc('axes', labelsize=MEDIUM_SIZE)    # fontsize of the x and y labels
plt.rc('xtick', labelsize=SMALL_SIZE)    # fontsize of the tick labels
plt.rc('ytick', labelsize=SMALL_SIZE)    # fontsize of the tick labels
plt.rc('legend', fontsize=SMALL_SIZE)    # legend fontsize
plt.rc('figure', titlesize=BIGGER_SIZE)  # fontsize of the figure title

n = len(ablation_types)

for ds in dataset_list:
    f, axes = plt.subplots(n, n, figsize=(5*n, 5*n))
    for i in range(n):
        x = ablation_types[i]
        sns.histplot(ablation_data[ds][x]['head_losses'].log().cpu(), ax=axes[i,i], legend=False)
        axes[i,i].set(xlabel=ax_labels[x])
        for j in range(i+1, n):
            y = ablation_types[j]
            plot_no_outliers(sns.scatterplot, .03, 
                            ablation_data[ds][x]['head_losses'].log(), 
                            ablation_data[ds][y]['head_losses'].log(),
                            axes[i,j], xy_line=True, args={"x": ax_labels[x], "y": ax_labels[y], "s": 20, "corr": True})
            plot_no_outliers(sns.scatterplot, 0, 
                             # ranks
                            (ablation_data[ds][x]['head_losses'] > ablation_data[ds][x]['head_losses'].squeeze(-1)).sum(dim=-1), 
                            (ablation_data[ds][y]['head_losses'] > ablation_data[ds][y]['head_losses'].squeeze(-1)).sum(dim=-1),
                            axes[j,i], xy_line=True, args={"x": ax_labels[x], "y": ax_labels[y], "s": 20, "corr": True})
    plt.suptitle(f"Correlation plots of ablation loss measurements on {dataset_list[ds]}")
    plt.tight_layout()
    plt.subplots_adjust(top=.96)
    plt.savefig(f"{plot_folder}/{ds}.png")
    plt.show()


# %%

CORR_SIZE = 12
SMALL_SIZE = 12
MEDIUM_SIZE = 14
BIGGER_SIZE = 18

plt.rc('font', size=CORR_SIZE)          # controls default text sizes
plt.rc('axes', titlesize=SMALL_SIZE)     # fontsize of the axes title
plt.rc('axes', labelsize=MEDIUM_SIZE)    # fontsize of the x and y labels
plt.rc('xtick', labelsize=SMALL_SIZE)    # fontsize of the tick labels
plt.rc('ytick', labelsize=SMALL_SIZE)    # fontsize of the tick labels
plt.rc('legend', fontsize=SMALL_SIZE)    # legend fontsize
plt.rc('figure', titlesize=BIGGER_SIZE)  # fontsize of the figure title

n_layers = 12
n_heads = 12
edges_per_node = {'attn': 146, 'mlp': 157}

def head_idx(layer_no, head_no):
    return (layer_no * n_heads + head_no).item()

# input is MLP 0, output is MLP 13
def mlp_idx(mlp_no):
    return (n_layers * n_heads + mlp_no).item()

for ds in dataset_list:
    edge_list = torch.load(f"results/pruning/{ds}/oa/acdc/edges_manual.pth")
    # edge_mask = edges_to_mask(edge_list)
    # _, _, _, attn_nodes, mlp_nodes = prune_dangling_edges(edge_mask)
    # node_list = {'attn': attn_nodes.squeeze(0).nonzero().tolist(), 'mlp': mlp_nodes.nonzero().flatten().tolist()}

    idx_dict = {i: [] for i in range(n_layers * n_heads + n_layers + 2)}

    # output is -2,
    for _, to_layer, to_head, from_layer, from_head in edge_list['attn-attn']:
        h1 = head_idx(to_layer, to_head)
        h2 = head_idx(from_layer, from_head)
        idx_dict[h1].append(h2)
        idx_dict[h2].append(h1)

    for _, to_layer, to_head, from_layer in edge_list['mlp-attn']:
        h = head_idx(to_layer, to_head)
        mlp = mlp_idx(from_layer)
        idx_dict[h].append(mlp)
        idx_dict[mlp].append(h)

    for to_layer, from_layer, from_head in edge_list['attn-mlp']:
        h = head_idx(from_layer, from_head)
        mlp = mlp_idx(to_layer)
        idx_dict[h].append(mlp)
        idx_dict[mlp].append(h)

    for to_layer, from_layer in edge_list['mlp-mlp']:
        mlp1 = mlp_idx(from_layer)
        mlp2 = mlp_idx(to_layer)
        idx_dict[mlp1].append(mlp2)
        idx_dict[mlp2].append(mlp1)
             
    for ablation_type in ablation_types:
        # input output nodes
        circuit = {n_layers * n_heads, n_layers * n_heads + n_layers + 1}

        heads_in_circ = [0 for _ in range(n_layers)]
        mlps_in_circ = [0 for _ in range(n_layers+2)]
        mlps_in_circ[0] = 1
        mlps_in_circ[-1] = 1

        true_positives = [0]
        false_positives = [0]
        roc = 0

        for node_idx in ablation_data[ds][ablation_type]['head_losses'].argsort(dim=0, descending=True).flatten().tolist():
            # mlp
            if node_idx >= n_layers * n_heads:
                node_idx += 1
                mlp_layer = node_idx - n_layers * n_heads
                edges_added = sum(mlps_in_circ) + sum(heads_in_circ[:mlp_layer]) + 3 * sum(heads_in_circ[mlp_layer:])
                mlps_in_circ[mlp_layer] += 1
            # attn
            else:
                head_layer = node_idx // n_heads

                # :n+1 gives (0,...,n): includes mlps 0,...,n-1
                edges_added = 3 * sum(mlps_in_circ[:head_layer+1]) + sum(mlps_in_circ[head_layer+1:]) + 3 * (sum(heads_in_circ) - heads_in_circ[head_layer])
                heads_in_circ[head_layer] += 1

            # print(edges_added)
            circuit.add(node_idx)

            # if len(circuit) > 3:
            #     break
            
            positives = 0
            for connected_node in idx_dict[node_idx]:
                if connected_node in circuit:
                    positives += 1
                    # print(connected_node)
            # print(positives)
            # print(idx_dict[node_idx])

            true_positives.append(true_positives[-1] + positives)
            false_positives.append(false_positives[-1])

            true_positives.append(true_positives[-1])
            false_positives.append(false_positives[-1] + edges_added - positives)

            roc += (true_positives[-1] + true_positives[-2]) * (false_positives[-1] - false_positives[-2]) / 2
        
        roc = round(roc / (true_positives[-1] * false_positives[-1]), 3)
        print(f"Ablation type {ablation_type} roc {roc}")
        
        sns.lineplot(x=false_positives, y=true_positives, label=ax_labels[ablation_type], estimator=None)
        
        # if ablation_type =="mean":
        #     break
    plt.xlabel("False positives")
    plt.ylabel("True positives")
    plt.xscale("log")
    # plt.xlim(0.01, 31526)
    plt.suptitle(f"Edge ROC curves on {dataset_list[ds]}")
    plt.savefig(f"{plot_folder}/{ds}_roc_nodes_log.png")
    plt.show()


# %%

# # Degree-weighted edges
# n_layers = 12
# n_heads = 12
# edges_per_node = {'attn': 146, 'mlp': 157}

# for ds in dataset_list:
#     edge_list = torch.load(f"results/pruning/{ds}/oa/acdc/edges_manual.pth")
#     edge_mask = edges_to_mask(edge_list)
#     _, _, _, attn_nodes, mlp_nodes = prune_dangling_edges(edge_mask)
#     node_list = {'attn': attn_nodes.squeeze(0).nonzero().tolist(), 'mlp': mlp_nodes.nonzero().flatten().tolist()}

#     idx_dict = {}
 
    
#     for layer_no, head_no in node_list['attn']:
#         idx_dict[layer_no * n_heads + head_no] = (attn_nodes[0, layer_no, head_no].item(), edges_per_node['attn'])
    
#     for mlp_no in node_list['mlp']:
#         idx_dict[n_layers * n_heads + mlp_no] = (mlp_nodes[0, mlp_no].item(), edges_per_node['mlp'])
        
#     for ablation_type in ablation_types:
#         true_positives = [0]
#         false_positives = [0]
#         roc = 0

#         for node_idx in ablation_data[ds][ablation_type]['head_losses'].argsort(dim=0, descending=True).flatten().tolist():
#             # if node_idx >= n_layers * n_heads:
#             #     continue            

#             if node_idx in idx_dict:
#                 true_positives.append(true_positives[-1] + 1)
#                 false_positives.append(false_positives[-1])

#                 # true_positives.append(true_positives[-1] + idx_dict[node_idx][0])
#                 # false_positives.append(false_positives[-1])
                
#                 # true_positives.append(true_positives[-1])
#                 # false_positives.append(false_positives[-1] + idx_dict[node_idx][1] - idx_dict[node_idx][0])
#             else:
#                 # true_positives.append(true_positives[-1])
#                 # false_positives.append(false_positives[-1] + edges_per_node['attn'] if node_idx < n_layers * n_heads else edges_per_node['mlp'])
#                 true_positives.append(true_positives[-1])
#                 false_positives.append(false_positives[-1] + 1)

#             roc += (true_positives[-1] + true_positives[-2]) * (false_positives[-1] - false_positives[-2]) / 2
        
#         roc = round(roc / (true_positives[-1] * false_positives[-1]), 3)
#         print(f"Ablation type {ablation_type} roc {roc}")
        
#         sns.lineplot(x=false_positives, y=true_positives, label=ablation_type, estimator=None)
#     plt.savefig(f"{folder}/{ds}_roc_nodes.png")
#     plt.show()

# %%
