# sphereface.py

import math
import numpy as np
import torch
import torch.nn as nn
from torch.autograd import Variable
from torch.nn import Parameter
import torch.nn.functional as F


__all__ = ['rgsage4', 'rgsage10', 'rgsage20',
           'rgsage36', 'rgsage64']


def conv3x3(in_planes, out_planes, stride=1):
    """3x3 convolution with padding"""
    return nn.Conv2d(in_planes, out_planes, kernel_size=3, stride=stride,
                     padding=1, bias=False)


def myphi(x, m):
    x = x * m
    output = 1 - x**2 / math.factorial(2) + x**4 / math.factorial(4) - \
        x**6 / math.factorial(6) + x**8 / math.factorial(8) - \
        x**9 / math.factorial(9)
    return output


class AngleLinear(nn.Module):
    def __init__(self, in_features, out_features, m=4, phiflag=True):
        super(AngleLinear, self).__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.weight = Parameter(torch.Tensor(in_features, out_features))
        self.weight.data.uniform_(-1, 1).renorm_(2, 1, 1e-5).mul_(1e5)
        self.phiflag = phiflag
        self.m = m
        self.mlambda = [
            lambda x: x**0,
            lambda x: x**1,
            lambda x: 2 * x**2 - 1,
            lambda x: 4 * x**3 - 3 * x,
            lambda x: 8 * x**4 - 8 * x**2 + 1,
            lambda x: 16 * x**5 - 20 * x**3 + 5 * x
        ]

    def forward(self, input):
        # size=(B,F)    F is feature len
        x = input

        # size=(F,Classnum) F=in_features Classnum=out_features
        w = self.weight

        ww = w.renorm(2, 1, 1e-5).mul(1e5)
        xlen = x.pow(2).sum(1).pow(0.5)  # size=B
        wlen = ww.pow(2).sum(0).pow(0.5)  # size=Classnum

        cos_theta = x.mm(ww)  # size=(B,Classnum)
        cos_theta = cos_theta / xlen.view(-1, 1) / wlen.view(1, -1)
        cos_theta = cos_theta.clamp(-1, 1)

        if self.phiflag:
            cos_m_theta = self.mlambda[self.m](cos_theta)
            theta = Variable(cos_theta.data.acos())
            k = (self.m * theta / 3.14159265).floor()
            n_one = k * 0.0 - 1
            phi_theta = (n_one**k) * cos_m_theta - 2 * k
        else:
            theta = cos_theta.acos()
            phi_theta = myphi(theta, self.m)
            phi_theta = phi_theta.clamp(-1 * self.m, 1)

        cos_theta = cos_theta * xlen.view(-1, 1)
        phi_theta = phi_theta * xlen.view(-1, 1)
        output = (cos_theta, phi_theta)
        return output  # size=(B,Classnum,2)


class BasicUnit(nn.Module):
    def __init__(self, planes):
        super(BasicUnit, self).__init__()
        self.planes = planes
        conv1 = conv3x3(self.planes, self.planes, stride=1)
        torch.nn.init.normal_(conv1.weight, std=0.01)
        conv2 = conv3x3(self.planes, self.planes, stride=1)
        torch.nn.init.normal_(conv2.weight, std=0.01)
        self.main = nn.Sequential(
            conv1,
            nn.PReLU(self.planes),
            conv2,
            nn.PReLU(self.planes)
        )

    def forward(self, x):
        y = self.main(x)
        y += x
        return y


class BasicBlock(nn.Module):
    def __init__(self, inplanes, outplanes, nlayers):
        super(BasicBlock, self).__init__()
        self.inplanes = inplanes
        self.outplanes = outplanes
        self.nlayers = nlayers

        self.conv1 = conv3x3(inplanes, outplanes, stride=2)
        torch.nn.init.xavier_normal_(self.conv1.weight)
        self.relu1 = nn.PReLU(outplanes)

        layers = []
        for i in range(nlayers):
            layers.append(BasicUnit(self.outplanes))
        self.main = nn.Sequential(*layers)

    def forward(self, x):
        x = self.relu1(self.conv1(x))
        x = self.main(x)
        return x


class SphereFace(nn.Module):
    def __init__(self, layers, nchannels=3, nfilters=64,
        ndim=512, nclasses=0, dropout_prob=0.0, features=False):
        super(SphereFace, self).__init__()
        self.nclasses = nclasses
        self.nfilters = nfilters
        self.nchannels = nchannels
        self.dropout_prob = dropout_prob
        self.features = features

        self.layer1 = BasicBlock(self.nchannels, 1 * nfilters, layers[0])
        self.layer2 = BasicBlock(1 * nfilters, 2 * nfilters, layers[1])
        self.layer3 = BasicBlock(2 * nfilters, 4 * nfilters, layers[2])
        self.layer4 = BasicBlock(4 * nfilters, 8 * nfilters, layers[3])

        self.fc = nn.Sequential(nn.Linear(8 * nfilters * 7 * 7, ndim),
            nn.BatchNorm1d(ndim, momentum=0.01, affine=True))
        torch.nn.init.xavier_normal_(self.fc[0].weight)

        if self.nclasses > 0:
            self.fc2 = nn.Linear(ndim,1)
            torch.nn.init.xavier_normal_(self.fc2.weight)

            # temp = 0.5+torch.Tensor(np.arange(nclasses-1))*torch.ones(nclasses-1)
            # self.scale = nn.parameter.Parameter(temp)
            # torch.nn.init.constant_(self.scale.data, 1.0)

    def forward(self, x):

        # x = self.relu(self.bn(self.conv(x)))

        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)

        x = x.view(x.size(0), -1)
        x = F.dropout(x, self.dropout_prob)
        feat = self.fc(x)
        # conf = self.conf(x)

        if self.nclasses > 0:
            if self.features is True:
                return [feat]
            else:
                y = self.fc2(feat)
                y = torch.sigmoid(y)
                return [feat, y]
        else:
            return [feat]


def rgsage4(**kwargs):
    """Constructs a SphereFace-04 model."""
    model = SphereFace([0, 0, 0, 0], **kwargs)
    return model


def rgsage10(**kwargs):
    """Constructs a SphereFace-10 model."""
    model = SphereFace([0, 1, 2, 0], **kwargs)
    return model


def rgsage20(**kwargs):
    """Constructs a SphereFace-20 model."""
    model = SphereFace([1, 2, 4, 1], **kwargs)
    return model


def rgsage36(**kwargs):
    """Constructs a SphereFace-36 model."""
    model = SphereFace([2, 4, 8, 2], **kwargs)
    return model


def rgsage64(**kwargs):
    """Constructs a SphereFace-64 model."""
    model = SphereFace([3, 8, 16, 3], **kwargs)
    return model
