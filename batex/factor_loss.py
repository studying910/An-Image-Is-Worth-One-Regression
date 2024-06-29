import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision.models import vgg16_bn
from torchvision import transforms


class FeatureHook:
    def __init__(self, module):
        self.features = None
        self.hook = module.register_forward_hook(self.on)

    def on(self, module, inputs, outputs):
        self.features = outputs

    def close(self):
        self.hook.remove()


class FeatureLoss(nn.Module):
    def __init__(self, loss, blocks, weights, device):
        super().__init__()
        self.feature_loss = loss
        self.device = device
        assert all(isinstance(w, (int, float)) for w in weights)
        assert len(weights) == len(blocks)

        self.weights = torch.tensor(weights).to(device)
        # VGG16 contains 5 blocks - 3 convolutions per block and 3 dense layers towards the end
        assert len(blocks) <= 5
        assert all(i in range(5) for i in blocks)
        assert sorted(blocks) == blocks

        vgg = vgg16_bn(pretrained=True).features
        vgg.eval()

        for param in vgg.parameters():
            param.requires_grad = False

        vgg = vgg.to(device)

        bns = [i - 2 for i, m in enumerate(vgg) if isinstance(m, nn.MaxPool2d)]
        assert all(isinstance(vgg[bn], nn.BatchNorm2d) for bn in bns)

        self.hooks = [FeatureHook(vgg[bns[i]]) for i in blocks]
        self.features = vgg[0: bns[blocks[-1]] + 1]

    def forward(self, inputs, targets):
        # normalize foreground pixels to ImageNet statistics for pre-trained VGG
        mean, std = [0.485, 0.456, 0.406], [0.229, 0.224, 0.225]
        trans = transforms.Normalize(mean, std).to(self.device)
        inputs = trans(inputs).to(self.device)
        targets = trans(targets).to(self.device)
        '''
        inputs = F.normalize(inputs, mean, std)
        targets = F.normalize(targets, mean, std)
        '''

        # extract feature maps
        self.features(inputs)
        input_features = [hook.features.clone() for hook in self.hooks]

        self.features(targets)
        target_features = [hook.features for hook in self.hooks]

        loss = torch.tensor(0.0,  device=self.device)

        # compare their weighted loss
        for lhs, rhs, w in zip(input_features, target_features, self.weights):
            lhs = lhs.view(lhs.size(0), -1)
            rhs = rhs.view(rhs.size(0), -1)
            loss += self.feature_loss(lhs, rhs) * w

        return loss


def mse_loss(x, y):
    return F.mse_loss(x, y)


def gram_matrix(x):
    c, hw = x.size()
    x = torch.mm(x, x.t()) / (c * hw)
    return x


def gram_loss(x, y):
    return F.mse_loss(gram_matrix(x), gram_matrix(y))
