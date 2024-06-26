# %%
import torch
from sys import argv
from functools import partial
import torch.optim
from pruners.VertexPruner import VertexPruner
from mask_samplers.MaskSampler import ConstantMaskSampler
from utils.MaskConfig import VertexInferenceConfig
from utils.task_datasets import get_task_ds
from utils.training_utils import load_model_data, LinePlot

# %%
# load model
model_name = "gpt2-small"
owt_batch_size = 10
device, model, tokenizer, owt_iter = load_model_data(model_name, owt_batch_size)
model.eval()
# model.cfg.use_attn_result = True
n_layers = model.cfg.n_layers
n_heads = model.cfg.n_heads

# settings
dataset = "ioi"
# dataset = argv[1]
folder=f"results/pruning_vertices_auto/{dataset}"

batch_size=50
pruning_cfg = VertexInferenceConfig(model.cfg, device, folder, batch_size=batch_size)
pruning_cfg.n_samples = 1

task_ds = get_task_ds(dataset, pruning_cfg.batch_size, device)
ds_test = task_ds.get_test_set(tokenizer)

for param in model.parameters():
    param.requires_grad = False

# %%
mask_sampler = ConstantMaskSampler()
vertex_pruner = VertexPruner(model, pruning_cfg, task_ds.init_modes(), mask_sampler)
vertex_pruner.add_patching_hooks()

# %%
next_batch = partial(task_ds.next_batch, tokenizer)
pruning_cfg.record_post_training(vertex_pruner, ds_test, next_batch, in_format="nodes")

# %%
