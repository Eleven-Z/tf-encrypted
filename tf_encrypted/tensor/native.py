from __future__ import absolute_import
from typing import Union, List, Dict, Tuple, Optional
import math

import numpy as np
import tensorflow as tf

from .factory import (AbstractFactory, AbstractTensor, AbstractVariable,
                      AbstractConstant, AbstractPlaceholder)
from .helpers import inverse
from .shared import binarize, conv2d, im2col
from ..operations.secure_random import seeded_random_uniform, random_uniform


def native_factory(NATIVE_TYPE, EXPLICIT_MODULUS):

    class Factory(AbstractFactory):

        def tensor(self, value):

            if isinstance(value, tf.Tensor):
                if value.dtype is not self.native_type:
                    value = tf.cast(value, dtype=self.native_type)
                return DenseTensor(value)

            if isinstance(value, np.ndarray):
                value = tf.convert_to_tensor(value, dtype=self.native_type)
                return DenseTensor(value)

            raise TypeError("Don't know how to handle {}".format(type(value)))

        def seeded_tensor(self, shape, seed):
            return SeededTensor(shape, seed)

        def constant(self, value):

            if isinstance(value, np.ndarray):
                value = tf.constant(value, dtype=self.native_type)
                return Constant(value)

            raise TypeError("Don't know how to handle {}".format(type(value)))

        def variable(self, initial_value):

            if isinstance(initial_value, (tf.Tensor, np.ndarray)):
                return Variable(initial_value)

            if isinstance(initial_value, DenseTensor):
                return Variable(initial_value.value)

            raise TypeError("Don't know how to handle {}".format(type(initial_value)))

        def placeholder(self, shape):
            return Placeholder(shape)

        @property
        def min(self):
            if EXPLICIT_MODULUS is not None:
                return 0
            else:
                return NATIVE_TYPE.min

        @property
        def max(self):
            if EXPLICIT_MODULUS is not None:
                return EXPLICIT_MODULUS
            else:
                return NATIVE_TYPE.max

        @property
        def modulus(self) -> int:
            if EXPLICIT_MODULUS is not None:
                return EXPLICIT_MODULUS
            else:
                return NATIVE_TYPE.max - NATIVE_TYPE.min + 1

        @property
        def native_type(self):
            return NATIVE_TYPE

        def sample_uniform(self,
                           shape,
                           minval: Optional[int] = None,
                           maxval: Optional[int] = None):
            minval = minval or self.min
            maxval = maxval or self.max  # TODO(Morten) believe this should be native_type.max+1
            value = random_uniform(shape=shape,
                                   dtype=self.native_type,
                                   minval=minval,
                                   maxval=maxval)
            return DenseTensor(value)

        def sample_bounded(self, shape, bitlength: int):
            maxval = 2 ** bitlength
            assert self.max > maxval
            value = random_uniform(shape=shape,
                                   dtype=self.native_type,
                                   minval=0,
                                   maxval=maxval)
            return DenseTensor(value)

        def sample_bits(self, shape):
            value = random_uniform(shape=shape,
                                   dtype=self.native_type,
                                   minval=0,
                                   maxval=2)
            return DenseTensor(value)

        def stack(self, xs: list, axis: int = 0):
            assert all(isinstance(x, DenseTensor) for x in xs)
            value = tf.stack([x.value for x in xs], axis=axis)
            return DenseTensor(value)

        def concat(self, xs: list, axis: int):
            assert all(isinstance(x, DenseTensor) for x in xs)
            value = tf.concat([x.value for x in xs], axis=axis)
            return DenseTensor(value)

    FACTORY = Factory()

    def _lift(x, y) -> Tuple['DenseTensor', 'DenseTensor']:

        if isinstance(x, DenseTensor) and isinstance(y, DenseTensor):
            return x, y

        if isinstance(x, DenseTensor) and isinstance(y, int):
            return x, x.factory.tensor(np.array([y]))

        if isinstance(x, DenseTensor) and isinstance(y, SeededTensor):
            return x, y.expand()

        if isinstance(x, int) and isinstance(y, DenseTensor):
            return y.factory.tensor(np.array([x])), y

        raise TypeError("Don't know how to lift {} {}".format(type(x), type(y)))

    class DenseTensor(AbstractTensor):

        def __init__(self, value: tf.Tensor) -> None:
            assert isinstance(value, tf.Tensor)
            self.value = value

        def to_native(self) -> tf.Tensor:
            return self.value

        def bits(self, factory=None) -> AbstractTensor:
            factory = factory or FACTORY
            if EXPLICIT_MODULUS is not None:
                bitsize = bitsize = math.ceil(math.log2(EXPLICIT_MODULUS))
                return factory.tensor(binarize(self.value % EXPLICIT_MODULUS, bitsize))
            else:
                return factory.tensor(binarize(self.value))

        def __repr__(self) -> str:
            return 'DenseTensor(shape={})'.format(self.shape)

        @property
        def shape(self):
            return self.value.shape

        @property
        def factory(self):
            return FACTORY

        def __add__(self, other):
            x, y = _lift(self, other)
            return x.add(y)

        def __radd__(self, other):
            x, y = _lift(self, other)
            return x.add(y)

        def __sub__(self, other):
            x, y = _lift(self, other)
            return x.sub(y)

        def __rsub__(self, other):
            x, y = _lift(self, other)
            return x.sub(y)

        def __mul__(self, other):
            x, y = _lift(self, other)
            return x.mul(y)

        def __rmul__(self, other):
            x, y = _lift(self, other)
            return x.mul(y)

        def __mod__(self, k: int):
            return self.mod(k)

        def __neg__(self):
            return self.mul(-1)

        def __getitem__(self, slice):
            return DenseTensor(self.value[slice])

        def add(self, other):
            x, y = _lift(self, other)
            value = x.value + y.value
            if EXPLICIT_MODULUS is not None:
                value %= EXPLICIT_MODULUS
            return DenseTensor(value)

        def sub(self, other):
            x, y = _lift(self, other)
            value = x.value - y.value
            if EXPLICIT_MODULUS is not None:
                value %= EXPLICIT_MODULUS
            return DenseTensor(value)

        def mul(self, other):
            x, y = _lift(self, other)
            value = x.value * y.value
            if EXPLICIT_MODULUS is not None:
                value %= EXPLICIT_MODULUS
            return DenseTensor(value)

        def matmul(self, other):
            x, y = _lift(self, other)
            value = tf.matmul(x.value, y.value)
            if EXPLICIT_MODULUS is not None:
                value %= EXPLICIT_MODULUS
            return DenseTensor(value)

        def im2col(self, h_filter: int, w_filter: int, padding: str, strides: int):
            return DenseTensor(im2col(self.value, h_filter, w_filter, padding, strides))

        def conv2d(self, other, strides: int, padding: str = 'SAME'):
            if EXPLICIT_MODULUS is not None:
                # TODO(Morten) any good reason this wasn't implemented for PrimeTensor?
                raise NotImplementedError()
            else:
                x, y = _lift(self, other)
                return conv2d(x, y, strides, padding)  # type: ignore

        def batch_to_space_nd(self, block_shape, crops):
            value = tf.batch_to_space_nd(self.value, block_shape, crops)
            return DenseTensor(value)

        def space_to_batch_nd(self, block_shape, paddings):
            value = tf.space_to_batch_nd(self.value, block_shape, paddings)
            return DenseTensor(value)

        def mod(self, k: int):
            value = self.value % k
            if EXPLICIT_MODULUS is not None:
                value %= EXPLICIT_MODULUS
            return DenseTensor(value)

        def transpose(self, perm):
            return DenseTensor(tf.transpose(self.value, perm))

        def strided_slice(self, args, kwargs):
            return DenseTensor(tf.strided_slice(self.value, *args, **kwargs))

        def split(self, num_split: int, axis: int = 0):
            values = tf.split(self.value, num_split, axis=axis)
            return [DenseTensor(value) for value in values]

        def reshape(self, axes: Union[tf.Tensor, List[int]]):
            return DenseTensor(tf.reshape(self.value, axes))

        def reduce_sum(self, axis, keepdims=None):
            value = tf.reduce_sum(self.value, axis, keepdims)
            if EXPLICIT_MODULUS is not None:
                value %= EXPLICIT_MODULUS
            return DenseTensor(value)

        def cumsum(self, axis, exclusive, reverse):
            value = tf.cumsum(self.value,
                              axis=axis,
                              exclusive=exclusive,
                              reverse=reverse)
            if EXPLICIT_MODULUS is not None:
                value %= EXPLICIT_MODULUS
            return DenseTensor(value)

        def equal_zero(self, factory=None):
            factory = factory or FACTORY
            return factory.tensor(tf.cast(tf.equal(self.value, 0),
                                          dtype=factory.native_type))

        def equal(self, other, factory=None):
            x, y = _lift(self, other)
            factory = factory or FACTORY
            return factory.tensor(tf.cast(tf.equal(x.value, y.value),
                                          dtype=factory.native_type))

        def truncate(self, amount, base=2):
            if base == 2:
                return self.right_shift(amount)
            else:
                factor = base**amount
                factor_inverse = inverse(factor, self.factory.modulus)
                return (self - (self % factor)) * factor_inverse

        def right_shift(self, bitlength):
            return DenseTensor(tf.bitwise.right_shift(self.value, bitlength))

        def expand_dims(self, axis: Optional[int] = None):
            return DenseTensor(tf.expand_dims(self.value, axis))

        def squeeze(self, axis: Optional[List[int]] = None):
            return DenseTensor(tf.squeeze(self.value, axis=axis))

        def negative(self):
            value = tf.negative(self.value)
            if EXPLICIT_MODULUS is not None:
                value %= EXPLICIT_MODULUS
            return DenseTensor(value)

        def cast(self, factory):
            return factory.tensor(self.value)

    class SeededTensor():
        @property
        def native_type(self):
            return FACTORY.native_type

        def __init__(self, shape, seed):
            self.seed = seed
            self.shape = shape

        def expand(self):
            backing = seeded_random_uniform(shape=self.shape,
                                            dtype=self.native_type,
                                            minval=self.native_type.min,
                                            maxval=self.native_type.max,
                                            seed=self.seed)
            return DenseTensor(backing)

    class Constant(DenseTensor, AbstractConstant):

        def __init__(self, constant: tf.Tensor) -> None:
            assert isinstance(constant, tf.Tensor)
            super(Constant, self).__init__(constant)

        def __repr__(self) -> str:
            return 'Constant(shape={})'.format(self.shape)

    class Placeholder(DenseTensor, AbstractPlaceholder):

        def __init__(self, shape: List[int]) -> None:
            self.placeholder = tf.placeholder(FACTORY.native_type, shape=shape)
            super(Placeholder, self).__init__(self.placeholder)

        def __repr__(self) -> str:
            return 'Placeholder(shape={})'.format(self.shape)

        def feed(self, value: np.ndarray) -> Dict[tf.Tensor, np.ndarray]:
            assert isinstance(value, np.ndarray), type(value)
            return {
                self.placeholder: value
            }

    class Variable(DenseTensor, AbstractVariable):

        def __init__(self, initial_value: Union[tf.Tensor, np.ndarray]) -> None:
            self.variable = tf.Variable(initial_value, dtype=FACTORY.native_type, trainable=False)
            self.initializer = self.variable.initializer
            super(Variable, self).__init__(self.variable.read_value())

        def __repr__(self) -> str:
            return 'Variable(shape={})'.format(self.shape)

        def assign_from_native(self, value: np.ndarray) -> tf.Operation:
            assert isinstance(value, np.ndarray), type(value)
            return self.assign_from_same(FACTORY.tensor(value))

        def assign_from_same(self, value: DenseTensor) -> tf.Operation:
            assert isinstance(value, DenseTensor), type(value)
            return tf.assign(self.variable, value.value).op

    return FACTORY
