import torch
import torch.nn as nn

EPS_L2_NORM = 1e-10


def desc_l2norm(desc):
    """descriptors with shape NxC or NxCxHxW"""

    desc = desc.view(desc.size(0),-1)
    desc = desc / desc.pow(2).sum(dim=1, keepdim=True).add(EPS_L2_NORM).pow(0.5)
    return desc


class FRN(nn.Module):
    def __init__(
        self, num_features, eps=1e-6, is_bias=True, is_scale=True, is_eps_leanable=False
    ):
        """
        FRN layer as in the paper
        Filter Response Normalization Layer: Eliminating Batch Dependence in the Training of Deep Neural Networks'
        <https://arxiv.org/abs/1911.09737>
        """
        super(FRN, self).__init__()

        self.num_features = num_features
        self.init_eps = eps
        self.is_eps_leanable = is_eps_leanable
        self.is_bias = is_bias
        self.is_scale = is_scale

        self.weight = nn.parameter.Parameter(
            torch.Tensor(1, num_features, 1, 1), requires_grad=True
        )
        self.bias = nn.parameter.Parameter(
            torch.Tensor(1, num_features, 1, 1), requires_grad=True
        )
        if is_eps_leanable:
            self.eps = nn.parameter.Parameter(torch.Tensor(1), requires_grad=True)
        else:
            self.register_buffer("eps", torch.Tensor([eps]))
        self.reset_parameters()

    def reset_parameters(self):
        nn.init.ones_(self.weight)
        nn.init.zeros_(self.bias)
        if self.is_eps_leanable:
            nn.init.constant_(self.eps, self.init_eps)

    def extra_repr(self):
        return "num_features={num_features}, eps={init_eps}".format(**self.__dict__)

    def forward(self, x):
        # Compute the mean norm of activations per channel.
        nu2 = x.pow(2).mean(dim=[2, 3], keepdim=True)
        nu2 = nu2.to(device=torch.device('cuda'))
        eps = self.eps.abs()
        eps = eps.to(device=torch.device('cuda'))
        # Perform FRN.
        x = x * torch.rsqrt(nu2 + eps)
        x = x.to(device=torch.device('cuda'))

        # Scale and Bias
        if self.is_scale:
            self_weight = self.weight
            self_weight = self_weight.to(device=torch.device('cuda'))
            x = self_weight * x
        if self.is_bias:
            self_bias = self.bias
            self_bias = self_bias.to(device=torch.device('cuda'))
            x = x + self_bias
        return x


class TLU(nn.Module):
    def __init__(self, num_features):
        """
        TLU layer as in the paper
        Filter Response Normalization Layer: Eliminating Batch Dependence in the Training of Deep Neural Networks'
        <https://arxiv.org/abs/1911.09737>
        """
        super(TLU, self).__init__()
        self.num_features = num_features
        self.tau = nn.parameter.Parameter(
            torch.Tensor(1, num_features, 1, 1), requires_grad=True
        )
        self.reset_parameters()

    def reset_parameters(self):
        # nn.init.zeros_(self.tau)
        nn.init.constant_(self.tau, -1)

    def extra_repr(self):
        return "num_features={num_features}".format(**self.__dict__)

    def forward(self, x):
        tau = self.tau
        tau = tau.to(device=torch.device('cuda'))
        return torch.max(x, tau)


class HyNet(nn.Module):
    """HyNet model definition"""

    def __init__(self, is_bias=True, is_bias_FRN=True, dim_desc=128, drop_rate=0.2):
        super(HyNet, self).__init__()
        self.dim_desc = dim_desc
        self.drop_rate = drop_rate

        self.layer1 = nn.Sequential(
            FRN(1, is_bias=is_bias_FRN),
            TLU(1),
            nn.Conv2d(1, 32, kernel_size=3, padding=1, bias=is_bias),
            FRN(32, is_bias=is_bias_FRN),
            TLU(32),
        )

        self.layer2 = nn.Sequential(
            nn.Conv2d(32, 32, kernel_size=3,stride=2, padding=1, bias=is_bias),
            FRN(32, is_bias=is_bias_FRN),
            TLU(32),
        )

        self.layer3 = nn.Sequential(
            nn.Conv2d(32, 64, kernel_size=3, stride=2, padding=1, bias=is_bias),
            FRN(64, is_bias=is_bias_FRN),
            TLU(64),
        )

        self.layer4 = nn.Sequential(
            nn.Conv2d(64, 64, kernel_size=3,stride=2, padding=1, bias=is_bias),
            FRN(64, is_bias=is_bias_FRN),
            TLU(64),
        )
        self.layer5 = nn.Sequential(
            nn.Conv2d(64, 128, kernel_size=3, stride=2, padding=1, bias=is_bias),
            FRN(128, is_bias=is_bias_FRN),
            TLU(128),
        )

        self.layer6 = nn.Sequential(
            nn.Conv2d(128, 128, kernel_size=3, padding=1, bias=is_bias),
            FRN(128, is_bias=is_bias_FRN),
            TLU(128),
        )

        self.layer7 = nn.Sequential(
            nn.Dropout(self.drop_rate),
            nn.Conv2d(128, self.dim_desc, kernel_size=8, bias=False),
            nn.BatchNorm2d(self.dim_desc, affine=False),
        )
    # def input_norm(self, x):
    #     flat = x.view(x.size(0), -1)
    #     mp = torch.mean(flat, dim=1)
    #     sp = torch.std(flat, dim=1) + 1e-7
    #     return (
    #         x - mp.detach().unsqueeze(-1).unsqueeze(-1).unsqueeze(-1).expand_as(x)
    #     ) / sp.detach().unsqueeze(-1).unsqueeze(-1).unsqueeze(1).expand_as(x)

    def forward(self, x, mode="eval"):
        #print(x.shape)
        for layer in [
            self.layer1,
            self.layer2,
            self.layer3,
            self.layer4,
            self.layer5,
            self.layer6,
        ]:
            x = layer(x)
            #print(x.shape)
        desc_raw = self.layer7(x).squeeze()
        #print(desc_raw.shape)
        desc_raw = desc_raw.view(desc_raw.size(0),-1)
        #print(desc_raw.shape)
        # desc_raw = self.layer7(x)
        # print(desc_raw.shape)
        # desc_raw = desc_raw.squeeze()
        # print(desc_raw.shape)
        # desc_raw = desc_raw.view(1, 128)

        desc = desc_l2norm(desc_raw)

        if mode == "train":
            return desc, desc_raw
        elif mode == "eval":
            return desc
