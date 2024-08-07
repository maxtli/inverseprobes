# %%
import pickle
import seaborn as sns
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator, AutoMinorLocator
from matplotlib.ticker import FormatStrFormatter
import glob
import os
import math
import pandas as pd
import numpy as np
import matplotlib.ticker as ticker

sns.set(rc={"xtick.bottom" : True, "ytick.left" : True})
# plt.rcParams.update({"xtick.bottom" : True, "ytick.left" : True})

# %%

# with open("results/pruning/gt/oa/hc/post_training.pkl", "rb") as f:
#     x = pickle.load(f)

# for i, l in enumerate(x["lamb"]):
#     if l == "0.0002":
#         for k in x:
#             x[k].pop(i)

# with open("results/pruning/gt/oa/hc/post_training.pkl", "wb") as f:
#     pickle.dump(x, f)


# %%

CORR_SIZE = 20
SMALL_SIZE = 18
MEDIUM_SIZE = 20
BIGGER_SIZE = 24

plt.rc('font', size=SMALL_SIZE)          # controls default text sizes
plt.rc('axes', titlesize=SMALL_SIZE)     # fontsize of the axes title
plt.rc('axes', labelsize=MEDIUM_SIZE)    # fontsize of the x and y labels
plt.rc('xtick', labelsize=SMALL_SIZE)    # fontsize of the tick labels
plt.rc('ytick', labelsize=SMALL_SIZE)    # fontsize of the tick labels
plt.rc('legend', fontsize=CORR_SIZE)    # legend fontsize
plt.rc('figure', titlesize=BIGGER_SIZE)  # fontsize of the figure title

plot_folder="plots_export/pareto"
task_lookup = {"ioi": "IOI", "ioi_baba": "IOI", "gt": "Greater-Than"}
ablation_lookup = {"mean": "mean", "cf": "counterfactual", "resample": "resample", "oa": "optimal"}

# %%

def plot_points(k, log_file, color=None, manual_only=False):
    with open(log_file, "rb") as f:
        log = pickle.load(f)
    # print(log)

    for i, lamb in enumerate(log['lamb']):
        if lamb == "manual":
            manual_run = {}
            for ke in log:
                manual_run[ke] = log[ke].pop(i)
            plt.plot(manual_run["clipped_edges"], manual_run["losses"], 'x', mew=7, markersize=15, color=color, label=None if manual_only else "manual")
     
    if manual_only:
        return

    loss_line = pd.DataFrame({
        "clipped_edges": log["clipped_edges"],
        "losses": log["losses"]
    }).sort_values("clipped_edges")
    loss_line["losses"] = loss_line["losses"].cummin()
        
    if color is not None:
        ax = sns.scatterplot(x=log["clipped_edges"], y=log["losses"], label=f"{k}", marker="o", s=30, color=color)
        ax = sns.lineplot(x=loss_line["clipped_edges"], y=loss_line["losses"], color=color, linewidth=1.5)
    else:
        ax = sns.scatterplot(x=log["clipped_edges"], y=log["losses"], label=f"{k}", marker="o", s=50)
    
    for i,t in enumerate(log['tau']):
        if 'vertices' in log:
            print(t, log["lamb"][i], log['clipped_edges'][i], log['vertices'][i], log['losses'][i])
        else:
            print(t, log["lamb"][i], log['clipped_edges'][i], log['losses'][i])
    return ax

# plt.rc('axes', titlesize=SMALL_SIZE)     # fontsize of the axes title
# plt.rc('axes', labelsize=20)    # fontsize of the x and y labels

def plot_pareto(pms, log=False, suffix="", order=None, manual=False):
    folder, y_bound, x_bound, task_name = pms

    fig = plt.figure(figsize=(8,8))
    method_list = set()                
    for k, (x, color) in folder.items():
        print(k)

        log_file = f"{x}/post_training{suffix}.pkl"
        if os.path.exists(log_file):
            plot_points(k, log_file, color)
            method_list.add(k)

        if manual:
            manual_log_file = f"{x.rsplit('/', 1)[0]}/acdc/post_training.pkl"
            if os.path.exists(manual_log_file):
                plot_points(k, manual_log_file, color, manual_only=True)           

        if os.path.exists(f"{x}/pre_training.pkl"):
            with open(f"{x}/pre_training.pkl", "rb") as f:
                log = pickle.load(f)
            print(log)
            sns.scatterplot(x=log["clipped_edges"], y=log["losses"], label="pre training", marker="X", s=50)

    plt.xlim(0,x_bound)
    # plt.gca().xaxis.set_major_locator(MultipleLocator(200)) # x gridlines every 0.5 units
    # plt.gca().xaxis.set_minor_locator(AutoMinorLocator(2)) # x gridlines every 0.5 units
    plt.minorticks_on()
    plt.tick_params(which='minor', bottom=False, left=False)
    # formatter = LogFormatter(labelOnlyBase=False, minor_thresholds=(2, 0.4))

    plt.grid(visible=True, which='major', color='grey', linewidth=0.5)
    plt.grid(visible=True, which='minor', color='darkgoldenrod', linewidth=0.3)
    # plt.gca().yaxis.set_major_locator(MultipleLocator(0.01)) # y gridlines every 0.5 units
    plt.xlabel(r"Edges in circuit $|\tilde{E}|$")
    plt.ylabel(r"Ablation loss gap $\Delta$")

    def myLogFormat(y,pos):
        # print(y)
        # Find the number of decimal places required
        # decimalplaces = int(np.maximum(-np.log10(y),0))     # =0 for numbers >=1
        decimalplaces = math.floor(np.log10(y))   # =0 for numbers >=1

        first_digit = str(round(y * 1000)).strip("0.")
        if len(first_digit) == 0:
            return
        if first_digit[0] != "1" and first_digit[0] != "5" and first_digit[0] != "2":
            return ""
        
        if decimalplaces >= 0:
            return first_digit[0] + "".join(decimalplaces * ["0"])
        else:
            # print("0." +  "".join((-1- decimalplaces) * ["0"]) + first_digit[0])
            return "0." +  "".join((-1- decimalplaces) * ["0"]) + first_digit[0]
        
    def majorF(y,pos):
        # Find the number of decimal places required
        decimalplaces = int(np.maximum(-np.log10(y),0))     # =0 for numbers >=1
        # Insert that number into a format string
        formatstring = '{{:.{:1d}f}}'.format(decimalplaces)
        # Return the formatted tick label
        print(formatstring.format(y))
        return formatstring.format(y)

    if log:
        plt.yscale("log")
        plt.gca().yaxis.set_major_formatter(ticker.FuncFormatter(myLogFormat))
        plt.gca().yaxis.set_minor_formatter(ticker.FuncFormatter(myLogFormat))
    else:
        plt.ylim(0,y_bound)

    s = task_name.split("/")
    t = s[0]
    a = s[-1]
    if a in ablation_lookup:
        abl_type = f"{ablation_lookup[a]} ablation"
    else:
        abl_type = f"ablation comparison"
    if order:
        handles, labels = plt.gca().get_legend_handles_labels()
        if "ACDC" in method_list:
            if labels[2] == "manual":
                h2 = plt.Line2D([0], [0], marker='x', markersize=8, mew=4, color=handles[2].get_color(), linestyle='None')
                handles[2] = h2
        if len(handles) == len(order):
            legend = plt.legend([handles[idx] for idx in order],[labels[idx] for idx in order], loc='upper right')
        else:
            plt.legend(loc="upper right")
    else:
        plt.legend(loc="upper right")

    plt.suptitle(f"{task_lookup[t]} circuits, {abl_type}")
    plt.tight_layout()

    plt.savefig(f"{plot_folder}/{task_name}_pt_{'log' if log else 'c'}{suffix}.png")
    plt.show()

# %%
l = [
    # ("ioi_baba", "cf", 0.2, 1200),
    # ("ioi_baba", "oa", 0.14, 1200),
    # ("ioi_baba", "mean", 1, 1200),
    # ("ioi_baba", "resample", 5, 1200),
    ("ioi", "cf", 0.2, 1800),
    ("ioi", "oa", 0.14, 1800),
    ("ioi", "mean", 1, 1800),
    ("ioi", "resample", 5, 1800),
    # ("ioi", "mean_agnostic", 1, 1200),
    # ("ioi", "resample_agnostic", 5, 1200),
    ("gt", "cf", 0.2, 800),
    ("gt", "oa", 0.04, 800), # need to deal with this
    ("gt", "mean", 0.4, 800),
    ("gt", "resample", 0.2, 800),
    # ("gt", "mean_agnostic", 0.2, 800),
    # ("gt", "resample_agnostic", 0.2, 800)
]
for dataset, ablation_type, x_bound, y_bound in l:
    root_folder = f"results/pruning/{dataset}/{ablation_type}"
    ax = None
    # reg_lambs = [2e-3, 1e-3, 7e-4, 5e-4, 2e-4, 1e-4]
    folders=({
            # "vertex": "results/pruning_vertices_auto/ioi", 
            "UGS (ours)": (f"{root_folder}/unif", "black"), 
            "HCGS": (f"{root_folder}/hc", "blue"), 
            # "edges uniform window": "results/pruning/ioi/cf/unif_window", 
            "ACDC": (f"{root_folder}/acdc", "crimson"),
            # "EP": (f"{root_folder}/ep", "purple"),
            "EAP": (f"{root_folder}/eap", "green")
        }, x_bound, y_bound, f"{dataset}/{ablation_type}")
    for log in [True]:
        plot_pareto(folders, log=log, order=[0,1,4,3,2])

# %%
# ablation comparison results
l2 = [
    ("ioi", 1, 1800),
    ("gt", 0.4, 800)
]
# comparison across ablation types
for dataset, x_bound, y_bound in l2:
    root_folder = f"results/pruning/{dataset}"
    ax = None
    # reg_lambs = [2e-3, 1e-3, 7e-4, 5e-4, 2e-4, 1e-4]
    folders=({
            "Mean": (f"{root_folder}/mean/unif", "indigo"), 
            "Resample": (f"{root_folder}/resample/unif", "olive"), 
            # "Mean-agnostic": (f"{root_folder}/mean_agnostic/unif", "purple"), 
            # "Resample-agnostic": (f"{root_folder}/resample_agnostic/unif", "green"), 
            "Optimal": (f"{root_folder}/oa/unif", "black"), 
            "CF": (f"{root_folder}/cf/unif", "maroon"), 
        }, x_bound, y_bound, f"{dataset}/comp/unif_")
    for log in [True]:
        plot_pareto(folders, log=log, order=[2,0,1,3], manual=True)

# %%

l3 = [
    ("ioi", "cf", 0.2, 1800),
    ("gt", "resample", 0.2, 800),
]
for dataset, ablation_type, x_bound, y_bound in l3:
    root_folder = f"results/pruning/{dataset}/{ablation_type}"
    ax = None
    # reg_lambs = [2e-3, 1e-3, 7e-4, 5e-4, 2e-4, 1e-4]
    folders=({
            # "vertex": "results/pruning_vertices_auto/ioi", 
            "UGS (ours)": (f"{root_folder}/unif", "black"), 
            "HCGS": (f"{root_folder}/hc", "blue"), 
            # "edges uniform window": "results/pruning/ioi/cf/unif_window", 
            # "ACDC": (f"{root_folder}/acdc", "crimson"),
            "EP": (f"{root_folder}/ep", "purple"),
            # "EAP": (f"{root_folder}/eap", "green")
        }, x_bound, y_bound, f"{dataset}/ep-demo/{ablation_type}")
    for log in [True]:
        plot_pareto(folders, log=log, order=[0,1,4,3,2])

# %%
datasets = {"ioi", "gt"}
ablation_types = {"cf", "oa", "mean", "resample"}
methods = {"acdc", "eap", "hc", "unif"}
my_df = []
for dataset, ablation_type, method in [
    (x,y,z) for x in datasets for y in ablation_types for z in methods
]:
    path = f"results/pruning/{dataset}/{ablation_type}/{method}/post_training.pkl"
    if os.path.exists(path):
        with open(path, "rb") as f:
            log = pickle.load(f)
        for i, l in enumerate(log['lamb']):
            my_df.append({
                "dataset": dataset,
                "ablation": ablation_type,
                "method": method,
                "lamb": l,
                "loss": log['losses'][i],
                "edges": log["clipped_edges"][i]
            })

df = pd.DataFrame(my_df)
df.to_csv(f"{plot_folder}/master.csv")

# %%
pd.set_option("display.max_rows", 200)
# display(df[df["lamb"] == "manual"])
# %%
ratio_df = df[(df["lamb"] == "manual") & (df["ablation"] == "oa")][["dataset","loss"]].merge(df[df["lamb"] == "manual"], on="dataset")
ratio_df["ratio"] = 1-ratio_df["loss_x"] / ratio_df["loss_y"]

# %%
IOI_MANUAL_EDGES = 963
below_df = df[(df["method"] == "unif") & (df["dataset"] == "ioi") & (df['edges'] < IOI_MANUAL_EDGES)].sort_values('edges', ascending=False).groupby("ablation").head(1)
above_df = df[(df["method"] == "unif") & (df["dataset"] == "ioi") & (df['edges'] > IOI_MANUAL_EDGES)].sort_values('edges', ascending=True).groupby("ablation").head(1)

comp_df = below_df.merge(above_df, on=["dataset", "ablation"])
comp_df['imputed'] = comp_df['loss_x'] + (comp_df['loss_y'] - comp_df['loss_x']) / (comp_df['edges_y'] - comp_df['edges_x']) * (IOI_MANUAL_EDGES - comp_df['edges_x'])

comp_df = comp_df.merge(ratio_df[["ablation", "loss_y", "dataset"]].rename(columns={"loss_y": "manual_loss"}), on=["dataset", "ablation"])
comp_df['pct'] = 1-comp_df['imputed'] / comp_df['manual_loss']
comp_df['imputed_ratio'] = 1 - comp_df.loc[comp_df["ablation"] == "oa", 'imputed'].iloc[0] / comp_df['imputed']
comp_df

# %%
GT_MANUAL_EDGES = 235
below_df = df[(df["method"] == "unif") & (df["dataset"] == "gt") & (df['edges'] <= GT_MANUAL_EDGES)].sort_values('edges', ascending=False).groupby("ablation").head(1)
above_df = df[(df["method"] == "unif") & (df["dataset"] == "gt") & (df['edges'] > GT_MANUAL_EDGES)].sort_values('edges', ascending=True).groupby("ablation").head(1)

comp_df = below_df.merge(above_df, on=["dataset", "ablation"])
comp_df['imputed'] = comp_df['loss_x'] + (comp_df['loss_y'] - comp_df['loss_x']) / (comp_df['edges_y'] - comp_df['edges_x']) * (GT_MANUAL_EDGES - comp_df['edges_x'])

comp_df = comp_df.merge(ratio_df[["ablation", "loss_y", "dataset"]].rename(columns={"loss_y": "manual_loss"}), on=["dataset", "ablation"])
comp_df['pct'] = 1-comp_df['imputed'] / comp_df['manual_loss']
comp_df['imputed_ratio'] = 1 - comp_df.loc[comp_df["ablation"] == "oa", 'imputed'].iloc[0] / comp_df['imputed']
comp_df

# %%

# %%
fp = 'results/pruning/ioi/oa/unif/post_training.pkl'
with open(fp, "rb") as f:
    l = pickle.load(f)

for i, x in enumerate(l['lamb']):
    if x == '0.03':
        for k in l:
            l[k].pop(i)

with open(fp, "wb") as f:
    pickle.dump(l, f)


# %%
l3 = [
    ("ioi", "cf", 0.2, 1200),
    ("ioi", "oa", 0.14, 1200),
    ("ioi", "mean", 1, 1200),
    ("ioi", "resample", 5, 1200),
    ("gt", "cf", 0.2, 800),
    ("gt", "oa", 0.04, 800), # need to deal with this
    ("gt", "mean", 0.4, 800),
    ("gt", "resample", 0.2, 800)
]
for dataset, ablation_type, x_bound, y_bound in l:
    root_folder = f"results/pruning/{dataset}"
    ax = None
    # reg_lambs = [2e-3, 1e-3, 7e-4, 5e-4, 2e-4, 1e-4]
    other_folders = {
            "Mean ablation": (f"{root_folder}/mean/acdc", "goldenrod"), 
            "Resample ablation": (f"{root_folder}/resample/acdc", "royalblue"), 
            "Optimal ablation": (f"{root_folder}/oa/acdc", "black"), 
            "Counterfactual": (f"{root_folder}/cf/acdc", "purple"), 
        }
    my_type = ablation_lookup[ablation_type].capitalize()
    print(my_type)
    folders=(other_folders, x_bound, y_bound, f"{dataset}/{ablation_type}")
    for log in [False, True]:
        plot_pareto(folders, log=log, suffix=f"_{ablation_type}")

# %%
folders=[
    ({
        # "vertex": "results/pruning_vertices_auto/ioi", 
        "edges HC": "results/pruning_edges_auto/hc", 
        # "edges HC (vertex prior)": "results/pruning/ioi/oa/vertex_prior", 
        "edges uniform": "results/pruning/ioi/oa/unif", 
        # "edges uniform window": "results/pruning/ioi/oa/unif_window", 
    }, {
        "ACDC": "results/pruning/ioi/oa/acdc",
        # "eap": "results/pruning/ioi/oa/eap"
    }, 0.15, 1500, "ioi"),
    ({
        # "vertex": "results/pruning_vertices_auto/gt", 
        # "edges HC": "results/pruning/gt/oa/edges", 
        # "edges HC (vertex prior)": "results/pruning/gt/oa/vertex_prior", 
        "edges uniform": "results/pruning/gt/oa/unif", 
    }, {
        "ACDC": "results/pruning/gt/oa/acdc",
        "eap": "results/pruning/gt/oa/eap"
    }, 0.05,1000,"gt"),
]


for folder in folders:
    plot_pareto(folder)

# %%


def compare_train_curves(folder_1, folder_2, edge_assn=False):
    edge_lookup_1 = {}
    edge_lookup_2 = {}
    if os.path.exists(f"{folder_1}/post_training.pkl"):
        with open(f"{folder_1}/post_training.pkl", "rb") as f:
            log = pickle.load(f)
        # print(log)
        for i, edges in enumerate(log['edges']):
            edge_lookup_1[log['lamb'][i]] = edges

    if os.path.exists(f"{folder_2}/post_training.pkl"):
        with open(f"{folder_2}/post_training.pkl", "rb") as f:
            log = pickle.load(f)
        # print(log)
        for i, edges in enumerate(log['edges']):
            edge_lookup_2[log['lamb'][i]] = edges
    
    print(edge_lookup_1)
    print(edge_lookup_2)
    edge_corr = {}
    if edge_assn:
        for lamb in edge_lookup_1:
            cur_edge_diff = 10000
            for lamb_2 in edge_lookup_2:
                edge_diff = abs(edge_lookup_2[lamb_2] - edge_lookup_1[lamb])
                if edge_diff < cur_edge_diff:
                    cur_edge_diff = edge_diff
                    edge_corr[lamb] = lamb_2

    for path in glob.glob(f"{folder_1}/*"):
        lamb = path.split("/")[-1]

        if not os.path.exists(f"{folder_1}/{lamb}/"):
            continue

        if edge_assn:
            lamb_2 = edge_corr[lamb]
        else:
            lamb_2 = lamb

        if os.path.exists(f"{folder_1}/{lamb}/fit_loss_log.pkl") and os.path.exists(f"{folder_2}/{lamb_2}/fit_loss_log.pkl"):
            with open(f"{folder_1}/{lamb}/fit_loss_log.pkl", "rb") as f:
                train_curve_1 = pickle.load(f)
            with open(f"{folder_2}/{lamb_2}/fit_loss_log.pkl", "rb") as f:
                train_curve_2 = pickle.load(f)
            
            if lamb in edge_lookup_1:
                print("edges control:", edge_lookup_1[lamb])
            if lamb_2 in edge_lookup_2:
                print("edges new:", edge_lookup_2[lamb_2])
            
            train_curve_1.compare_plot("kl_loss", 50, train_curve_2, f"Post training comparison {lamb}", start=500)
        
        if os.path.exists(f"{folder_1}/{lamb}/metadata.pkl") and os.path.exists(f"{folder_2}/{lamb_2}/metadata.pkl"):
            with open(f"{folder_1}/{lamb}/metadata.pkl", "rb") as f:
                train_curve_1 = pickle.load(f)[0]
            with open(f"{folder_2}/{lamb_2}/metadata.pkl", "rb") as f:
                train_curve_2 = pickle.load(f)[0]
            train_curve_1.compare_plot("kl_loss", 50, train_curve_2, f"Training comparison {lamb}", start=300)

            train_curve_1.compare_plot("complexity_loss", 50, train_curve_2, f"Training comparison {lamb}", start=300)
# %%
# comparing ioi with diverse dataset to templated dataset
compare_train_curves("results/pruning/ioi/oa/b_unif_wrong_4", "results/pruning-5-6/ioi_edges_unif")

# %%
compare_train_curves("results/pruning/ioi/oa/dynamic_unif", "results/pruning/ioi/oa/unif_correct")

# %%

compare_train_curves("results/pruning/ioi/oa/dynamic_unif-0.5", "results/pruning/ioi/oa/dynamic_unif-0.99")



# %%
# unif: ZERO node reg, detached bottom derivative
# wrong: attached bottom derivative with zero node reg
# wrong_2: attached bottom derivative, fixed node_reg to 5e-3
# wrong_3: attached bottom derivative, fixed node_reg to 5e-4
# correct: scaling node_reg, detached bottom derivative

# wrong_3 ioi_b: corrected diverse dataset predicting first token of IO
# wrong_4 ioi_b: wrong diverse dataset, sometimes predicting IO completion

# ioi scale invariance: fix overweighting of tail probs (dividing by smaller window)
# scale_var: fix overweighting of bottom
compare_train_curves("results/pruning-5-6/gt_edges_unif", "results/pruning/gt/oa/unif_wrong")

# %%
# 
compare_train_curves("results/pruning/ioi/oa/acdc", "results/pruning/ioi/oa/unif", edge_assn=True)