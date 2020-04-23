import numpy as np
from opt_einsum import contract


class ConvLayer:
    def __init__(self, dim_in, f, c, stride, pad, activation='relu', lamb=0.0):
        """Initialise the convolutional layer of the neural network.
        """
        self.dim_in = dim_in
        self.dim_out = (dim_in[0],
                        1+int((dim_in[1]-f + 2*pad)/stride),
                        1+int((dim_in[2]-f + 2*pad)/stride),
                        c)
        self.f = f
        self.n_c = c
        self.stride = stride
        self.pad = pad
        self.activation = activation
        self.lamb = lamb
        self.X = np.zeros(dim_in)
        self.Z = np.zeros(self.dim_out)
        self.W = self.__init_weights(f, c, dim_in)
        self.b = np.zeros((1, 1, 1, c))
        self.dX = np.zeros(self.X.shape)
        self.dW = np.zeros(self.W.shape)
        self.db = np.zeros(self.b.shape)
        self.vW = np.zeros(self.W.shape)
        self.vb = np.zeros(self.b.shape)
        self.sW = np.zeros(self.W.shape)
        self.sb = np.zeros(self.b.shape)
        self.dZ_pad = self._allocate_dZ_pad(dim_in[0])

    def __init_weights(self, f, c, dim_in):
        """Initialise parameters He initialisation."""
        return np.random.randn(f, f, dim_in[-1], c) \
            * np.sqrt(2/(dim_in[1]*dim_in[2]))

    def _relu(self, z):
        """ReLu activation function
        Args:
            z (np.array): input
        Returns:
            np.array.
        """
        return np.maximum(0, z)

    def _deriv_relu(self, z):
        """Derivative of ReLu function
        Args:
            z (np.array): input values
        Returns:
            np.array: derivative at z.
        """
        return np.float64(z > 0)

    def _allocate_dZ_pad(self, m):
        """Allocate memory for the padded dZ values for 
        the transposed convolution in the backpropagation."""
        in_h = self.X.shape[1] + (self.W.shape[0]-1)
        in_w = self.X.shape[2] + (self.W.shape[0]-1)
        dZ_pad = np.zeros((m, in_h,
                           in_w, self.Z.shape[-1]))
        return dZ_pad

    def forward(self, X):
        """Forward propagation
        Args:
            x (np.array): array of dimension dim_in (m, n_h_p, n_w_p, n_c_p)
        Returns:
            np.array: output.
        """
        self.X = X.copy()
        n_h = int((X.shape[1] - self.f + 2*self.pad) / self.stride) + 1
        n_w = int((X.shape[2] - self.f + 2*self.pad) / self.stride) + 1

        if self.pad != 0:
            x_pad = np.pad(X, ((0, 0), (self.pad, self.pad),
                               (self.pad, self.pad), (0, 0)),
                           mode='constant', constant_values=(0, 0))
        else:
            x_pad = X

        # compute Z for multiple input images and multiple filters
        shape = (self.f, self.f, self.dim_in[-1], X.shape[0], n_h, n_w, 1)
        strides = (x_pad.strides * 2)[1:]
        strides = (*strides[:-3], strides[-3]*self.stride,
                   strides[-2]*self.stride, strides[-1])
        M = np.lib.stride_tricks.as_strided(
            x_pad, shape=shape, strides=strides, writeable=False)
        self.Z = contract('pqrs,pqrtbmn->tbms', self.W, M)
        self.Z = self.Z + self.b
        if self.activation == 'relu':
            return self._relu(self.Z)
        elif self.activation == 'none':
            return self.Z

    def conv_backward(self, dA):
        """Naive backward propagation implementation
        Args:
            dA (np.array): gradient of output values
        Returns:
            np.array: dX gradient of input values
        """
        self.dW[:, :, :, :] = 0
        self.db[:, :, :, :] = 0
        (m, n_h, n_w, n_c) = dA.shape
        x_pad = np.pad(self.X, ((0, 0), (self.pad, self.pad), (self.pad, self.pad),
                                (0, 0)), mode='constant', constant_values=(0, 0))
        dx_pad = np.pad(self.dX, ((0, 0), (self.pad, self.pad), (self.pad, self.pad),
                                  (0, 0)), mode='constant', constant_values=(0, 0))
        dZ = dA * self._deriv_relu(self.Z)
        for h in range(n_h):
            v_s = h*self.stride
            v_e = h*self.stride + self.f
            for w in range(n_w):
                h_s = w*self.stride
                h_e = w*self.stride + self.f
                for c in range(n_c):
                    dx_pad[:, v_s:v_e, h_s:h_e, :] += self.W[:, :, :, c] \
                        * dZ[:, h, w, c].reshape(dZ.shape[0], 1, 1, 1)
                    self.dW[:, :, :, c] += np.sum(x_pad[:, v_s:v_e, h_s:h_e]
                                                  * dZ[:, h, w, c].reshape(dZ.shape[0], 1, 1, 1),
                                                  axis=0)
                    self.db[:, :, :, c] += np.sum(dZ[:, h, w, c])
        self.dX[:, :, :, :] = dx_pad[:, self.pad:-
                                     self.pad, self.pad:-self.pad, :]

        self.dW += self.lamb/self.dim_in[0]*self.W

        return self.dX

    def backward(self, dA):
        """Numpy einsum and stride tricks backward propagation implementation.
        Args:
            dA (np.array): gradient of output values
        Returns:
            np.array: dX gradient of input values
        """
        if len(dA.shape) == 2:
            dZ = dA.reshape(dA.shape[1], *self.dim_out[1:]
                            ) * self._deriv_relu(self.Z)
        else:
            dZ = dA * self._deriv_relu(self.Z)
        if dZ.shape[0] != self.dZ_pad.shape[0]:
            self.dZ_pad = self._allocate_dZ_pad(dZ.shape[0])
        self.dW[:, :, :, :] = 0
        self.db[:, :, :, :] = 0
        (m, n_H_prev, n_W_prev, n_C_prev) = self.X.shape
        (f, f, n_C_prev, n_C) = self.W.shape
        stride = self.stride
        pad = self.pad
        (m, n_H, n_W, n_C) = dZ.shape
        W_rot = np.rot90(self.W, 2)
        pad_dZ = self.W.shape[0]-(self.pad+1)
        if pad_dZ == 0:
            self.dZ_pad[:, 0::stride, 0::stride] = dZ
        else:
            self.dZ_pad[:, pad_dZ:-pad_dZ:stride,
                        pad_dZ:-pad_dZ:stride, :] = dZ

        shape = (self.dZ_pad.shape[0],                       # m
                 self.dZ_pad.shape[1] - W_rot.shape[0] + 1,  # X_nx
                 self.dZ_pad.shape[2] - W_rot.shape[1] + 1,  # X_ny
                 self.dZ_pad.shape[3],                       # dZ_nc
                 W_rot.shape[0],                             # f
                 W_rot.shape[1])                             # f
        strides = (self.dZ_pad.strides[0],
                   self.dZ_pad.strides[1],
                   self.dZ_pad.strides[2],
                   self.dZ_pad.strides[3],
                   self.dZ_pad.strides[1],
                   self.dZ_pad.strides[2])
        M = np.lib.stride_tricks.as_strided(
            self.dZ_pad, shape=shape, strides=strides, writeable=False,)
        self.dX = contract('pqrs,bmnspq->bmnr', W_rot, M)

        X_pad = np.pad(self.X, ((0, 0), (pad, pad), (pad, pad), (0, 0)), mode='constant',
                       constant_values=(0, 0))
        shape_Z = (f, f, n_C_prev, m, n_H, n_W)
        strides_Z = (X_pad.strides)[1:] + (X_pad.strides)[0:3]
        strides_Z = (*strides_Z[:-2], strides_Z[-2]
                     * stride, strides_Z[-1]*stride)

        M = np.lib.stride_tricks.as_strided(
            X_pad, shape=shape_Z, strides=strides_Z, writeable=False)
        self.dW = contract('abcd,pqsabc->pqsd', dZ, M)
        self.dW += self.lamb/self.dim_in[0]*self.W

        self.db = contract('abcd->d', dZ).reshape(1, 1, 1, n_C)

        return self.dX

    def update_parameters(self, rate, t, beta1=0.9, beta2=0.999, epsilon=1e-8):
        """Update parameters"""
        self.vW = beta1*self.vW + (1-beta1)*self.dW
        self.vb = beta1*self.vb + (1-beta1)*self.db
        self.sW = beta2*self.sW + (1-beta2)*self.dW**2
        self.sb = beta2*self.sb + (1-beta2)*self.db**2
        vW = self.vW / (1-beta1**t)
        vb = self.vb / (1-beta1**t)
        sW = self.sW / (1-beta2**t)
        sb = self.sb / (1-beta2**t)
        self.W -= rate * vW/(np.sqrt(sW)+epsilon)
        self.b -= rate * vb/(np.sqrt(sb)+epsilon)
