import jax.numpy as jnp
from flax import linen as nn
from typing import Any, Callable
from functools import partial
from jax import random
import numpy as onp
import jax
from IPython import embed
import jax.nn.initializers as init
ModuleDef = Any

kaiming_normal = partial(init.variance_scaling, 2.0, "fan_out",
                         "truncated_normal")


def dilated_conv3x3(x,
                    features,
                    strides=1,
                    groups=1,
                    dilation=1,
                    name='dilated_conv3x3'):
    """3x3 convolution with padding"""
    d = max(1, dilation)
    return nn.Conv(features,
                   kernel_size=(3, 3),
                   strides=(strides, strides),
                   padding=((dilation, dilation), (dilation, dilation)),
                   kernel_dilation=(d, d),
                   feature_group_count=groups,
                   use_bias=False,
                   name=name)(x)


def conv1x1(features, stride=1):
    """1x1 convolution"""
    return nn.Conv(features=features,
                   kernel_size=(1, 1),
                   strides=(stride, stride),
                   padding='VALID',
                   use_bias=False)


class Bottleneck(nn.Module):
    """Bottleneck ResNet block."""
    expansion = 4
    #__constants__ = ['downsample']
    features: int
    # norm_layer: ModuleDef
    strides: int = 1
    downsample: Any = None
    groups: int = 1
    base_width: int = 64

    dilation: int = 1
    norm_layer: ModuleDef = nn.BatchNorm
    dtype: Any = jnp.float32

    def setup(self):
        self.width = int(
            (self.features * (self.base_width / 64.)) * self.groups)
        self.norm_layer1 = nn.BatchNorm(self.width,
                                        scale_init=nn.initializers.ones,
                                        bias_init=nn.initializers.zeros)
        self.norm_layer2 = nn.BatchNorm(self.features * self.expansion,
                                        scale_init=nn.initializers.ones,
                                        bias_init=nn.initializers.zeros)

    @nn.compact
    def __call__(self, x):
        width = int(self.features * (self.base_width / 64.)) * self.groups
        identity = x

        #1
        out = conv1x1(width)(x)
        out = self.norm_layer1(out)  # width
        out = nn.relu(out)

        #2
        out = dilated_conv3x3(out,
                              width,
                              strides=self.strides,
                              groups=self.groups,
                              dilation=self.dilation,
                              name='conv2')
        out = self.norm_layer1(out)  # width
        out = nn.relu(out)

        #3
        out = conv1x1(self.features * self.expansion)(
            out)  # ie self.features * 4
        out = self.norm_layer2(out)  # self.features * self.expansion

        if self.downsample:
            identity = self.downsample(x)

        out += identity
        out = nn.relu(out)
        return out


class BasicBlock(nn.Module):
    expansion = 1
    __constants__ = ['downsample']
    features: int
    # norm: Any = nn.BatchNorm
    strides: int = 1
    downsample: Any = None
    groups: int = 1
    base_width: int = 64
    dilation: int = 1
    dtype: Any = jnp.float32

    def setup(self):
        self.norm = nn.BatchNorm(self.features,
                                 scale_init=nn.initializers.ones,
                                 bias_init=nn.initializers.zeros)

    @nn.compact
    def __call__(self, inputs):
        if self.groups != 1 or self.base_width != 64:
            raise ValueError(
                'BasicBlock only supports groups=1 and base_width=64')
        if self.dilation > 1:
            raise NotImplementedError(
                "Dilation > 1 not supported in BasicBlock")

        identity = inputs
        out = dilated_conv3x3(inputs,
                              self.features,
                              strides=self.strides,
                              name='conv1')
        out = self.norm_layer(out)
        out = nn.relu(out)

        out = dilated_conv3x3(out, self.features, name='conv2')
        out = self.norm(out)

        if self.downsample is not None:
            identity = self.downsample(out)

        out += identity
        out = nn.relu(out)

        return out


class AANetFeature(nn.Module):
    in_channels = int = 32
    groups: int = 1
    width_per_group: int = 64
    feature_mdconv: bool = True
    norm_layer: Callable = nn.BatchNorm

    def setup(self):

        # self.inplanes = 64
        self.inplanes = self.in_channels
        self.dilation = 1

        #self.groups = self.groups
        self.base_width = self.width_per_group

    def apply_layer(self, x, block, planes, blocks, stride=1, dilate=False):
        downsample = None
        previous_dilation = self.dilation
        # dilation = self.dilation
        if dilate:
            self.dilation *= stride  #TODO: local variable: dilation
            stride = 1

        if stride != 1 or self.inplanes != planes * block.expansion:

            def downsample(x):
                out = conv1x1(planes * block.expansion, stride)(x)
                out = self.norm_layer(use_running_average=False)(out)
                return out

        x = block(planes, stride, downsample, self.groups,
                  self.width_per_group, previous_dilation, self.norm_layer)(x)

        #TODO: find work-around for this bc immutable... do we use this tho
        #self.inplanes = planes * block.expansion

        for _ in range(1, blocks):
            x = block(planes,
                      groups=self.groups,
                      base_width=self.base_width,
                      dilation=self.dilation,
                      norm_layer=self.norm_layer)(x)

        return x

    @nn.compact
    def __call__(self, x):  # TODO: call
        stride = 3

        x = nn.Conv(self.inplanes,
                    kernel_size=(7, 7),
                    strides=(stride, stride),
                    padding=((3, 3), (3, 3)),
                    use_bias=False,
                    kernel_init=kaiming_normal(dtype=jnp.float64))(x)
        x = nn.BatchNorm(use_running_average=False,
                         scale_init=nn.initializers.ones,
                         bias_init=nn.initializers.zeros)(x)
        x = nn.relu(x)  # H/3

        layers = [3, 4, 6]  # ResNet-40

        layer1 = self.apply_layer(x, Bottleneck, self.in_channels,
                                  layers[0])  # H/3
        layer2 = self.apply_layer(x,
                                  Bottleneck,
                                  self.in_channels * 2,
                                  layers[1],
                                  stride=2)  # H/6

        #block = DeformBottleneck if self.feature_mdconv else Bottleneck
        block = Bottleneck  # TODO: change this back to above
        layer3 = self.apply_layer(x,
                                  block,
                                  self.in_channels * 4,
                                  layers[2],
                                  stride=2)  # H/12

        return [layer1, layer2, layer3]


class FeaturePyramid(nn.Module):
    in_channel: int = 32

    @nn.compact
    def __call__(self, x):
        # x: [B, H, W, 32]
        # out1 = [B, H/2, W/2, 64]
        out1 = nn.Conv(self.in_channel * 2,
                       kernel_size=(3, 3),
                       strides=(2, 2),
                       padding=((1, 1), (1, 1)),
                       use_bias=False)(x)
        out1 = nn.BatchNorm(self.in_channel * 2)(out1)
        out1 = nn.leaky_relu(out1, negative_slope=0.2)
        out1 = nn.Conv(self.in_channel * 2,
                       kernel_size=(1, 1),
                       strides=(1, 1),
                       padding='VALID',
                       use_bias=False)(out1)
        out1 = nn.BatchNorm(self.in_channel * 2)(out1)
        out1 = nn.leaky_relu(out1, negative_slope=0.2)

        # out2 = [B, H/4, W/4, 128]
        out2 = nn.Conv(self.in_channel * 4,
                       kernel_size=(3, 3),
                       strides=(2, 2),
                       padding=((1, 1), (1, 1)),
                       use_bias=False)(out1)
        out2 = nn.BatchNorm(self.in_channel * 4)(out2)
        out2 = nn.leaky_relu(out2, negative_slope=0.2)
        out2 = nn.Conv(self.in_channel * 4,
                       kernel_size=(1, 1),
                       strides=(1, 1),
                       padding='VALID',
                       use_bias=False)(out2)
        out2 = nn.BatchNorm(self.in_channel * 4)(out2)
        out2 = nn.leaky_relu(out2, negative_slope=0.2)

        return [x, out1, out2]


class FeaturePyramidNetwork(nn.Module):
    #in_channels: list    TODO: uncomment this when done testing
    out_channels: int = 128
    num_levels: int = 3

    # FPN paper uses 256 out channels by default
    def setup(self):
        self.in_channels = [
            32, 64, 128
        ]  # TODO: remove this hardcoded default value after testing and uncomment above TODO

    #     assert isinstance(self.in_channels, list)

    @nn.compact
    #TODO: currently testing w the 3 layers manually, in reality only 1 parameter: inputs
    def __call__(self, in1, in2, in3):
        #TODO: remove this hardcoded value after testing and use replace all "inp" w "inputs"
        inp = [in1, in2, in3]

        # Inputs: resolution high -> low

        assert isinstance(
            self.in_channels, tuple
        )  #TODO: replace w below (should be list but its keeps converting my list to tuple)
        # assert isinstance(self.in_channels, list)

        assert len(self.in_channels) == len(inp)

        #TODO: original appends to this lateral_convs which gets the module list... does this mean length can be greater than 3?
        # if so, we my proposed rewriting (to be usable w flax may not work as intended...)
        # lateral_convs = nn.ModuleList()
        # fpn_convs = nn.ModuleList()

        # build laterals
        laterals = []
        for i in range(self.num_levels):
            lateral = nn.Conv(self.out_channels,
                              kernel_size=(1, 1),
                              kernel_init=init.xavier_uniform(),
                              bias_init=nn.initializers.zeros)(inp[i])
            laterals.append(lateral)

        #embed()

        # Build top-down path
        used_backbone_levels = len(laterals)
        for i in range(used_backbone_levels - 1, 0, -1):
            b, h, w, c = laterals[i].shape
            laterals[i -
                     1] += jax.image.resize(laterals[i],
                                            shape=(b, h * 2, w * 2, c),
                                            method=jax.image.ResizeMethod.
                                            NEAREST)  # upscale by factor of 2
            #F.interpolate(laterals[i], scale_factor=2, mode='nearest')

        # Build output w laterals + fpn
        out = []
        for i in range(used_backbone_levels):
            fpn = nn.Conv(self.out_channels,
                          kernel_size=(3, 3),
                          padding=((1, 1), (1, 1)),
                          kernel_init=init.xavier_uniform(),
                          bias_init=nn.initializers.zeros)(laterals[i])
            fpn = nn.BatchNorm(self.out_channels)(fpn)
            fpn = nn.relu(fpn)
            out.append(fpn)

        return out


key1, key2 = random.split(random.PRNGKey(0), 2)
# x = random.uniform(key1, (15, 32, 32, 3))  # for AANet
# init_variables = model.init(key2, x)
#
# feature_extractor = AANetFeature(feature_mdconv=(not False))
# x = random.uniform(key1, (15, 3, 32, 32))  # for AANet
# init_variables = feature_extractor.init(key2, x)

max_disp = 200 // 3  # randomly picked

key3, key4 = random.split(random.PRNGKey(0), 2)

model = FeaturePyramidNetwork()  #inchannels
x = random.uniform(key3, (15, 128, 128, 3))  # for AANet
x2 = random.uniform(key3, (15, 64, 64, 3))
x3 = random.uniform(key3, (15, 32, 32, 3))

init_pyramid = model.init(key4, x, x2, x3)


# Testing in jitted context
@jax.jit
def apply(variables, _x):
    return model.apply(variables, x)


from flax.core import freeze, unfreeze

print('initialized parameter shapes:\n',
      jax.tree_map(jnp.shape, unfreeze(init_pyramid)))
