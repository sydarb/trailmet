
import torch
import torch.nn as nn
import torch.nn.init as init
from tqdm import tqdm_notebook
from ..algorithms import BaseAlgorithm

class BaseQuantization(BaseAlgorithm):
    """base class for quantization algorithms"""
    def __init__(self, **kwargs):
        super(BaseQuantization, self).__init__(**kwargs)
        pass

    def quantize(self, model, dataloaders, method, **kwargs):
        pass
    
    def round_ste(x: torch.Tensor):
        """
        Implement Straight-Through Estimator for rounding operation.
        """
        return (x.round() - x).detach() + x

    def get_calib_samples(self, train_loader, num_samples):
        """
        Get calibration-set samples for finetuning weights and clipping parameters
        """
        calib_data = []
        for batch in train_loader:
            calib_data.append(batch[0])
            if len(calib_data)*batch[0].size(0) >= num_samples:
                break
        return torch.cat(calib_data, dim=0)[:num_samples]
    
    
class StraightThrough(nn.Module):
    """used to place an identity function in place of a non-differentail operator for gradient calculation"""
    def __int__(self):
        super().__init__()
        pass

    def forward(self, input):
        return input

class RoundSTE(torch.autograd.Function):
    @staticmethod
    def forward(ctx, input):
        output = torch.round(input)
        return output

    @staticmethod
    def backward(ctx, grad_output):
        return grad_output
    
class FoldBN():
    """used to fold batch norm to prev linear or conv layer which helps reduce comutational overhead during quantization"""
    def __init__(self):
        pass

    def _fold_bn(self, conv_module, bn_module):
        w = conv_module.weight.data
        y_mean = bn_module.running_mean
        y_var = bn_module.running_var
        safe_std = torch.sqrt(y_var + bn_module.eps)
        w_view = (conv_module.out_channels, 1, 1, 1)
        if bn_module.affine:
            weight = w * (bn_module.weight / safe_std).view(w_view)
            beta = bn_module.bias - bn_module.weight * y_mean / safe_std
            if conv_module.bias is not None:
                bias = bn_module.weight * conv_module.bias / safe_std + beta
            else:
                bias = beta
        else:
            weight = w / safe_std.view(w_view)
            beta = -y_mean / safe_std
            if conv_module.bias is not None:
                bias = conv_module.bias / safe_std + beta
            else:
                bias = beta
        return weight, bias


    def fold_bn_into_conv(self, conv_module, bn_module):
        w, b = self._fold_bn(conv_module, bn_module)
        if conv_module.bias is None:
            conv_module.bias = nn.Parameter(b)
        else:
            conv_module.bias.data = b
        conv_module.weight.data = w
        # set bn running stats
        bn_module.running_mean = bn_module.bias.data
        bn_module.running_var = bn_module.weight.data ** 2


    def is_bn(self, m):
        return isinstance(m, nn.BatchNorm2d) or isinstance(m, nn.BatchNorm1d)


    def is_absorbing(self, m):
        return (isinstance(m, nn.Conv2d)) or isinstance(m, nn.Linear)


    def search_fold_and_remove_bn(self, model: nn.Module):
        """
        method to recursively search for batch norm layers, absorb them into 
        the previous linear or conv layers, and set it to an identity layer 
        """
        model.eval()
        prev = None
        for n, m in model.named_children():
            if self.is_bn(m) and self.is_absorbing(prev):
                self.fold_bn_into_conv(prev, m)
                # set the bn module to straight through
                setattr(model, n, StraightThrough())
            elif self.is_absorbing(m):
                prev = m
            else:
                prev = self.search_fold_and_remove_bn(m)
        return prev
