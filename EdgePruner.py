import torch
import numpy as np 
from tqdm import tqdm
from fancy_einsum import einsum
import math
from functools import partial
import torch.optim
import time
import seaborn as sns
import matplotlib.pyplot as plt
import pickle
import torch.nn.functional as F
from training_utils import LinePlot

kl_loss = torch.nn.KLDivLoss(reduction="none")

class EdgePruner(torch.nn.Module):
    def __init__(self, 
                 model, 
                 pruning_cfg, 
                 init_modes,
                 mask_sampler, 
                 node_reg=0,
                 parallel_inference=False, 
                 inference_mode=False, 
                 cache_compressed_attn=True, 
                 ablation_backward=False
                 ):
        super().__init__()
        self.base_model = model
        self.pruning_cfg = pruning_cfg
        self.mask_sampler = mask_sampler
        self.circs = ["q", "k", "v"]


        init_modes_attention, init_modes_mlp = init_modes
        self.modal_attention = torch.nn.Parameter(init_modes_attention)
        self.modal_mlp = torch.nn.Parameter(init_modes_mlp)
        self.attention_cache = []
        self.mlp_cache = []
        self.node_reg = node_reg
        self.cache_compressed_attn = cache_compressed_attn
        self.inference_mode = inference_mode
        self.parallel_inference = parallel_inference
        self.ablation_backward = ablation_backward
        self.disable_hooks = False

        if not self.cache_compressed_attn:
            model.cfg.use_attn_result = True

        self.post_bias = torch.stack([self.base_model.blocks[layer_no].attn.b_O.clone().detach() for layer_no in range(self.base_model.cfg.n_layers)], dim=0)

        if cache_compressed_attn:
            self.W_O = torch.stack([self.base_model.blocks[layer_no].attn.W_O.clone().detach() for layer_no in range(self.base_model.cfg.n_layers)], dim=0)
        
        columns =  ['kl_loss', 'complexity_loss', 'temp', 'temp_cond', 'temp_count', 'temp_reg']
        if node_reg > 0:
            columns.append("node_loss")
        self.log = LinePlot(columns)

        self.cache_hooks = self.get_cache_hooks()
        self.patching_hooks = self.get_patching_hooks()

        self.last_token_mask = None
        
    def set_log(self, log):
        self.log = log

    def add_cache_hooks(self):
        for name, hook in self.cache_hooks.values():
            self.base_model.add_hook(name, hook)
    
    def add_patching_hooks(self):
        for name, hook in self.patching_hooks.values():
            self.base_model.add_hook(name, hook)

    def cache_hook_all_tokens(self, storage, activations, hook):
        if self.disable_hooks:
            return
        
        if self.parallel_inference:
            bsz = self.pruning_cfg.batch_size
            storage.append(activations[bsz:])
            return activations[:bsz]
        else:
            storage.append(activations)
            return activations

    # attention_constants: list of all constants for attention for layers thus far
    # mlp_constants: list of all constants for embed+mlp layers thus far
    # attention_cache: contains all attentions stored thus far, list of attention outputs by later
    # mlp_cache: list of mlp outputs by layer
    def pruning_edge_attention_hook_all_tokens(self, layer_no, circ_idx, orig_in, hook):
        if self.disable_hooks:
            return orig_in

        def prepend_orig(out):
            if self.parallel_inference:
                return torch.cat([orig_in[:self.pruning_cfg.batch_size], out], dim=0)
            return out
        # i is the current layer (0-indexed, equal to the number of layers before this one)
        # orig_in: batch x seq_pos x d_model
        # prune_mask[0]: (bsz * n_samples) x n_heads (dest) x i x n_heads (source)
        # attention_constants: i x n_heads (source) x d_model
        # attention_cache: i * [(bsz * n_samples) x seq_pos x n_heads (source) x d_model]

        # mlp_constants: (i+1) x d_model
        # mlp_cache: (i+1) * [(bsz * n_samples) x seq_pos x d_model]

        # mlp_mask: (bsz * n_samples) x 1 (seq_pos) x n_heads (dest) x i x 1 (d_model)
        mlp_mask = self.mask_sampler.sampled_mask["mlp-attn"][layer_no][:,circ_idx].unsqueeze(1).unsqueeze(-1)
        
        out = (mlp_mask * torch.stack(self.mlp_cache, dim=-2).unsqueeze(dim=2)).sum(dim=-2)
        # print((out-orig_in).square().sum())

        if not self.ablation_backward:
            out = out + ((1-mlp_mask) * self.modal_mlp[:layer_no+1]).sum(dim=-2)

        if layer_no == 0:
            return prepend_orig(out)
        
        # print(len(self.mask_sampler.sampled_mask["attn-attn"]))
        # print(layer_no)
        # print((self.mask_sampler.sampled_mask["attn-attn"][layer_no].shape))

        # (bsz * n_samples) x 1 (seq_pos) x n_heads (dest) x i x n_heads (source) x 1 (d_model/d_head)
        attn_mask = self.mask_sampler.sampled_mask["attn-attn"][layer_no][:,circ_idx].unsqueeze(1).unsqueeze(-1)
        attn_term = attn_mask * torch.stack(self.attention_cache, dim=-3).unsqueeze(dim=2)

        # W_O: source_head x d_head x d_model
        if self.cache_compressed_attn:
            attn_term = einsum(
                        "batch pos dest_head prev_layer source_head d_head, \
                            prev_layer source_head d_head d_model -> \
                            batch pos dest_head d_model",
                        attn_term,
                        self.W_O[:layer_no]
                )
        else:
            attn_term = attn_term.sum(dim=[-3,-2])
        out = out + attn_term + self.post_bias[:layer_no].sum(dim=0)

        if self.ablation_backward:
            return prepend_orig(out + self.modal_attention[layer_no])

        return prepend_orig(out + ((1-attn_mask) * self.modal_attention[:layer_no]).sum(dim=[-3,-2]))

    # same as attentions except not parallelized
    # attention_constants: list of all constants for attention for layers thus far
    # mlp_constants: list of all constants for embed+mlp layers thus far
    # attention_cache: contains all attentions stored thus far, list of attention outputs by later
    # mlp_cache: list of mlp outputs by layer
    def pruning_edge_mlp_hook_all_tokens(self, layer_no, orig_in, hook): 
        if self.disable_hooks:
            return orig_in
            
        def prepend_orig(out):
            if self.parallel_inference:
                return torch.cat([orig_in[:self.pruning_cfg.batch_size], out], dim=0)
            return out
        # i is the current layer (0-indexed, equal to the number of layers before this one)
        # orig_in: batch x seq_pos x d_model
        # prune_mask[0]: (bsz * n_samples) x i x n_heads
        # attention_constants: i x n_heads x d_model
        # attention_cache: i * [(bsz * n_samples) x seq_pos x n_heads x d_model]

        # mlp_constants: (i+1) x d_model
        # mlp_cache: (i+1) * [(bsz * n_samples) x seq_pos x d_model]

        # (bsz * n_samples) x 1 (seq_pos) x i x 1 (d_model)
        n_layers = self.base_model.cfg.n_layers
        attn_layers_before = min(layer_no+1,n_layers)
        mlp_mask = self.mask_sampler.sampled_mask["mlp-mlp"][layer_no].unsqueeze(1).unsqueeze(-1)

        out = (mlp_mask * torch.stack(self.mlp_cache, dim=2)).sum(dim=2)

        if not self.ablation_backward:
            out = out + ((1-mlp_mask) * self.modal_mlp[:layer_no+1]).sum(dim=2)

        # print((out - mlp_cache[0]).square().sum())
        
        # (bsz * n_samples) x 1 (seq_pos) x i x n_heads x 1 (d_model)
        attn_mask = self.mask_sampler.sampled_mask["attn-mlp"][layer_no].unsqueeze(1).unsqueeze(-1)
        attn_term = attn_mask * torch.stack(self.attention_cache, dim=-3)

        # W_O: source_head x d_head x d_model
        if self.cache_compressed_attn:
            attn_term = einsum(
                        "batch pos prev_layer source_head d_head, \
                            prev_layer source_head d_head d_model -> \
                            batch pos d_model",
                        attn_term,
                        self.W_O[:attn_layers_before]
                )
        else:
            attn_term = attn_term.sum(dim=[-3,-2])

        out = out + attn_term + self.post_bias[:attn_layers_before].sum(dim=0)

        if self.ablation_backward:
            return prepend_orig(out + self.modal_mlp[layer_no])
        
        return prepend_orig(out + ((1-attn_mask) * self.modal_attention[:attn_layers_before]).sum(dim=[2,3]))

    def pruning_edge_final_hook_all_tokens(self, orig_in, hook):
        out = self.pruning_edge_mlp_hook_all_tokens(self.base_model.cfg.n_layers, orig_in, hook)
        if (self.inference_mode and not self.parallel_inference) or self.disable_hooks:
            out = out.unsqueeze(0)
        else:
            out = out.unflatten(0, (-1, self.pruning_cfg.batch_size))
        out = (out * self.last_token_mask.unsqueeze(-1)).sum(dim=2)
        return out

    def get_cache_hooks(self):
        embed_filter = lambda name: name == f"blocks.{0}.hook_resid_pre"
        attention_points_filter = lambda layer_no, name: name == f"blocks.{layer_no}.attn.hook_result"
        attention_compressed_filter = lambda layer_no, name: name == f"blocks.{layer_no}.attn.hook_z"
        mlp_points_filter = lambda layer_no, name: name == f"blocks.{layer_no}.hook_mlp_out"

        n_layers = self.base_model.cfg.n_layers

        return {
            # cache embedding
            "cache_embed": 
            (embed_filter, 
            partial(self.cache_hook_all_tokens, 
                    self.mlp_cache)),

            # cache attention (at z if compressed)
            **{
                f"cache_attn_{layer_no}": 
                (partial(attention_compressed_filter 
                         if self.cache_compressed_attn 
                         else attention_points_filter, layer_no), 
                partial(self.cache_hook_all_tokens, self.attention_cache)) 
                for layer_no in range(n_layers)
            },

            # cache MLP
            **{
                f"cache_mlp_{layer_no}": 
                (partial(mlp_points_filter, layer_no), 
                partial(self.cache_hook_all_tokens, self.mlp_cache)) 
                for layer_no in range(n_layers)
            }
        }
    
    def time_hook(self, name, f, *args, **kwargs):
        end = []
        for i in range(2):
            end.append(torch.cuda.Event(enable_timing=True))
        end[0].record()

        x = f(*args, **kwargs)
        end[1].record()
        torch.cuda.synchronize()

        print(name, end[0].elapsed_time(end[1]))

        return x

    def get_patching_hooks(self):
        attention_in_filter = lambda layer_no, circ, name: name == f"blocks.{layer_no}.hook_{circ}_input"
        mlp_in_filter = lambda layer_no, name: name == f"blocks.{layer_no}.hook_mlp_in"
        final_embed_filter = lambda name: name == f"blocks.{n_layers-1}.hook_resid_post"

        n_layers = self.base_model.cfg.n_layers
        
        return {
            # patch attention (recompute O-matrix if compressed)
            **{
                f"patch_attn_{layer_no}_{circ}": 
                (partial(attention_in_filter, layer_no, circ), 
                # partial(self.time_hook, f"attn_{layer_no}", 
                partial(self.pruning_edge_attention_hook_all_tokens, 
                        layer_no, j)) 
                for layer_no in range(n_layers) 
                for j, circ in enumerate(self.circs)
            },

            # patch MLP (recompute O-matrix if compressed)
            **{
                f"patch_mlp_{layer_no}": 
                (partial(mlp_in_filter, layer_no), 
                # partial(self.time_hook, f"mlp_{layer_no}", 
                partial(self.pruning_edge_mlp_hook_all_tokens, 
                        layer_no)) 
                for layer_no in range(n_layers)
            },

            # patch MLP (recompute O-matrix if compressed)
            "patch_final": 
            (final_embed_filter, 
                # partial(self.time_hook, f"output_embeds", 
                        self.pruning_edge_final_hook_all_tokens)
                # )
        }
    
    def early_term(self, decline_pct=.03):
        if self.log.t < 500:
            return 0
        
        kl_loss_decl, _ = self.log.stat_sig_growth("kl_loss")
        complex_loss_decl, _ = self.log.stat_sig_growth("complexity_loss")
        temp = self.log.stat_book["temp"][-1]

        if kl_loss_decl < 0.01 and complex_loss_decl < decline_pct and temp < 1e-2:
            self.log.early_term_count += 1
        else:
            self.log.early_term_count = max(0, self.log.early_term_count - 2)
        return self.log.early_term_count

    def get_modes(self):
        return torch.cat([self.modal_attention.flatten(start_dim=0,end_dim=1), self.modal_mlp], dim=0)
    
    def complexity_loss(self, sampling_params):
        return (sampling_params[...,0]-sampling_params[...,1].relu() * (math.log(-self.pruning_cfg.hard_concrete_endpoints[0]/self.pruning_cfg.hard_concrete_endpoints[1]))).sigmoid()
    
    def node_reg_loss(self):
        n_layers = self.base_model.cfg.n_layers
        n_heads = self.base_model.cfg.n_heads
        node_losses_out = torch.zeros((n_layers, n_heads)).to(self.pruning_cfg.device)
        node_losses_in = []
        for i,ts in enumerate(self.mask_sampler.sampling_params['attn-attn']):
            pad_layers = n_layers - i
            # 3 (dest_circ), 12 (dest head idx), i (prev n_layers), 12 (prev head idx), 2 (0-location and 1-temperature)
            layer_loss = self.complexity_loss(ts)
            node_losses_out = node_losses_out + F.pad(layer_loss, (0,0,0,pad_layers), "constant", 0).sum(dim=[0,1])
            node_losses_in.append(layer_loss.sum(dim=[0,2,3]))

        for i, ts in enumerate(self.mask_sampler.sampling_params['attn-mlp']):
            pad_layers = max(0, n_layers - i-1)
            # i (prev n_layers), 12 (prev head idx), 2 (0-location and 1-temperature)
            layer_loss = self.complexity_loss(ts)
            node_losses_out = node_losses_out + F.pad(layer_loss, (0,0,0,pad_layers), "constant", 0)
        
        for i, ts in enumerate(self.mask_sampler.sampling_params['mlp-attn']):
            layer_loss = self.complexity_loss(ts)
            node_losses_in[i] = node_losses_in[i] + layer_loss.sum(dim=[0,2])
        
        return F.tanh(2 * (node_losses_out + torch.stack(node_losses_in, dim=0)) / (n_layers * n_heads))        
                
    def forward(self, batch, last_token_pos, graph_suffix=None, complexity_mean=False, return_output=False, timing=True):
        
        if timing:
            end = []
            for x in range(6):
                end.append(torch.cuda.Event(enable_timing=True))
            end[0].record()

        all_sampling_params = self.mask_sampler()
        
        self.attention_cache.clear()
        self.mlp_cache.clear()

        with torch.no_grad():
            last_token_mask = torch.zeros_like(batch).to(self.pruning_cfg.device)
            last_token_mask[torch.arange(last_token_mask.shape[0]), last_token_pos] = 1
        
        self.last_token_mask = last_token_mask

        if timing:
            end[1].record()
        
        n_samples = 1 if self.inference_mode else self.pruning_cfg.n_samples
        pruned_output = self.base_model(
            batch.repeat(n_samples+(1 if self.parallel_inference else 0),1)
        ).log_softmax(dim=-1)

        if timing:
            end[2].record()

        if return_output:
            if timing: 
                torch.cuda.synchronize()
                print("Cuda time", end[1].elapsed_time(end[2]))
            return pruned_output
        
        if self.parallel_inference:
            orig_output = pruned_output[0]
            pruned_output = pruned_output[1:]
        else:
            self.disable_hooks = True
            with torch.no_grad():
                orig_output = self.base_model(batch)
                orig_output = orig_output.log_softmax(dim=-1)
            self.disable_hooks = False
        
        if timing:
            end[3].record()
            torch.cuda.synchronize()
            for i in range(1,4):
                print("Cuda time", end[i-1].elapsed_time(end[i]))

        kl_losses = kl_loss(pruned_output, orig_output.exp()).sum(dim=-1)
        # io_loss = target_results - ablated_results

        if self.inference_mode:
            return kl_losses

        # alphas already logged
        complexity_loss = self.complexity_loss(all_sampling_params)
                    
        temperature_loss = all_sampling_params[...,1].square()

        temp_c = self.pruning_cfg.temp_scheduler(self.log)

        loss = kl_losses.mean() + self.pruning_cfg.lamb * complexity_loss.sum() + temp_c * temperature_loss.sum()

        if self.node_reg > 0:
            node_reg_loss = self.node_reg_loss()
            loss += self.node_reg * node_reg_loss.sum()

        # end[4].record()

        with torch.no_grad():
            avg_temp = all_sampling_params[...,1].relu().mean().item()
            temp_cond = torch.nan_to_num((all_sampling_params[...,1]-1).relu().sum() / (all_sampling_params[:,1] > 1).sum(), nan=0, posinf=0, neginf=0).item() + 1
            temp_count = (2*all_sampling_params[...,1].relu().sigmoid()-1).mean().item()

            log_entry = {
                "kl_loss": kl_losses.mean().item(), 
                "complexity_loss": complexity_loss.mean().item() if complexity_mean else complexity_loss.sum().item(),
                "temp": avg_temp,
                "temp_cond": temp_cond,
                "temp_count": temp_count,
                "temp_reg": temp_c
            }
            if self.node_reg > 0:
                log_entry["node_loss"] = node_reg_loss.sum().item()

            self.log.add_entry(log_entry)

            if graph_suffix is not None:
                j = graph_suffix
                sns.histplot(kl_losses.flatten().detach().cpu())
                plt.savefig(f"{self.pruning_cfg.folder}/kl-loss{j}.png")
                plt.close()

                sns.histplot(torch.cat([ts.flatten() for k in self.mask_sampler.sampled_mask for ts in self.mask_sampler.sampled_mask[k]], dim=0).detach().flatten().cpu())
                plt.savefig(f"{self.pruning_cfg.folder}/mask{j}.png")
                plt.close()

                sns.histplot(x=all_sampling_params[:,0].sigmoid().detach().flatten().cpu(), y=all_sampling_params[:,1].detach().flatten().cpu(), bins=100)
                plt.savefig(f"{self.pruning_cfg.folder}/params-probs{j}.png")
                plt.close()

                sns.histplot(x=all_sampling_params[:,0].detach().flatten().cpu(), y=all_sampling_params[:,1].detach().flatten().cpu(), bins=100)
                plt.savefig(f"{self.pruning_cfg.folder}/params-logits{j}.png")
                plt.close()

            print("KL:", kl_losses.mean().item())
            print("Complexity:", complexity_loss.sum().item(), "out of", complexity_loss.nelement())

            if self.node_reg:
                print("Node complexity:", node_reg_loss.sum().item())

            print("Avg temperature", avg_temp)
            print("Avg temp > 1", temp_cond)
            print("Temp count", temp_count)

        # end[5].record()

        # Waits for everything to finish running
        # torch.cuda.synchronize()

        # for i in range(1,4):
        #     print("Cuda time", end[i-1].elapsed_time(end[i]))

        return loss, all_sampling_params