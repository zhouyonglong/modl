import numpy as np
import scipy
from numpy import linalg
from sklearn.utils import check_array
from sklearn.utils import check_random_state, gen_batches

from sklearn.linear_model import cd_fast

from modl._utils.enet_proj_fast import enet_norm_fast, enet_projection_fast

from modl._utils.randomkit.random_fast import Sampler

from modl._utils.enet_proj import enet_scale


class DictFactSlow:
    mask_sampling_c = {'bernouilli': 1,
                     'cycle': 2,
                     'fixed': 3}

    def __init__(self,
                 reduction=1,
                 learning_rate=1,
                 sample_learning_rate=0.76,
                 BC_agg='async',
                 Dx_agg='masked',
                 G_agg='masked',
                 code_alpha=1,
                 code_l1_ratio=1,
                 n_epochs=1,
                 n_components=10,
                 batch_size=10,
                 code_pos=False,
                 comp_pos=False,
                 mask_sampling='cycle',
                 random_state=None,
                 comp_l1_ratio=0,
                 verbose=0,
                 callback=None):
        self.batch_size = batch_size
        self.learning_rate = learning_rate
        self.sample_learning_rate = sample_learning_rate
        self.BC_agg = BC_agg
        self.Dx_agg = Dx_agg
        self.G_agg = G_agg
        self.code_l1_ratio = code_l1_ratio
        self.code_alpha = code_alpha
        self.reduction = reduction
        self.comp_l1_ratio = comp_l1_ratio

        self.mask_sampling = mask_sampling

        self.comp_pos = comp_pos
        self.code_pos = code_pos

        self.n_components = n_components
        self.n_epochs = n_epochs

        self.random_state = random_state

        self.verbose = verbose
        self.callback = callback

    def fit(self, X):
        """
        Compute the matrix factorisation X ~ components_ x code_,solving for
        D, code_ = argmin_{r2 ||D^j ||_1 + (1 - r2) || D^j ||_2^2 < 1}
        1 / 2 || X - D A ||_2 + (1 - r) || A ||_2 / 2 + r || A ||_1
        Parameters
        ----------
        X:  n_samples * n_features

        Returns
        -------

        """
        X = check_array(X, order='C', dtype='float')
        # Main loop
        n_features, n_samples = X.shape
        for _ in range(self.n_epochs):
            permutation = self.random_state_.permutation(n_features)
            X = X[permutation]
            if self.G_agg == 'average':
                self.G_average = self.G_average[permutation]
            if self.Dx_agg == 'average':
                self.Dx_average = self.Dx_average[permutation]
                self.code_ = self.code_[permutation]
            self.col_counter_ = self.col_counter_[permutation]

            batches = gen_batches(self.batch_size, n_samples)
            for batch in batches:
                len_batch = batch.stop - batch.start
                self.counter_ += len_batch
                self.col_counter_[batch] += 1
                this_X = X[batch]
                this_G_average = self.G_average_[batch]\
                    if self.G_agg == 'average' else None
                this_Dx_average = self.Dx_average_[
                    batch] if self.Dx_agg == 'average' else None
                this_code = self.code_[batch]
                this_col_counter = self.col_counter_[batch]

                self.batch_fit(this_Dx_average, this_G_average,
                                 this_X, this_code, this_col_counter)
        return self

    def prepare(self, n_samples=None, n_features=None, X=None):
        if X is not None:
            X = check_array(X, order='C', dtype='float')
            # Transpose to fit usual column streaming
            n_samples, n_features = X.shape
        else:
            if n_features is None or n_samples is None:
                raise ValueError('Either provide'
                                 'shape or data to function prepare.')

        # Regression statistics
        if self.G_agg == 'average':
            self.G_average_ = np.zeros((n_samples, self.n_components,
                                        self.n_components))
        if self.Dx_agg == 'average':
            self.Dx_average_ = np.zeros((n_samples, self.n_components))
        # Dictionary statistics
        self.C_ = np.zeros((self.n_components, self.n_components))
        self.B_ = np.zeros((self.n_components, n_features))
        self.gradient_ = np.zeros((self.n_components, n_features))

        self.random_state_ = check_random_state(self.random_state)
        if X is None:
            self.components_ = self.random_state_.randn(self.n_components,
                                                        n_features)
        else:
            random_idx = self.random_state_.permutation(n_samples)[
                         :self.n_components]
            self.components_ = X[random_idx].copy()
        self.components_ = enet_scale(self.components_,
                                      l1_ratio=self.comp_l1_ratio,
                                      radius=1)

        self.code_ = np.empty((n_samples, self.n_components))
        self.code_[:] = 1. / self.n_components

        self.comp_norm_ = np.zeros(self.n_components)

        if self.G_agg == 'full':
            self.G_ = self.components_.T.dot(self.components_).T
        else:
            self.G_ = np.zeros((self.n_components, self.n_components),
                               order='F')
        # self.G_ is Fortran-continuous

        self.counter_ = 0
        self.col_counter_ = np.zeros(n_samples, dtype='int')
        self.random_state_ = check_random_state(self.random_state)
        random_seed = self.random_state_.randint(np.iinfo(np.uint32).max)
        self.row_sampler_ = Sampler(n_features, self.reduction,
                                    DictFactSlow.mask_sampling_c[
                                        self.mask_sampling],
                                    random_seed)

    def batch_fit(self, this_Dx_average, this_G_average, this_X,
                    this_code, this_col_counter):
        subset = self.row_sampler_.yield_subset()
        w_sample = np.power(this_col_counter, -self.sample_learning_rate)
        w = pow(self.counter_, -self.learning_rate)
        self.compute_code(this_X, self.components_, self.G_,
                          this_G_average, this_Dx_average, this_code,
                          w_sample,
                          subset)
        if self.BC_agg == 'full':
            self.update_BC(this_X, this_code, self.C_, self.B_,
                           self.gradient_, w, subset)
            self.update_dict(self.components_, self.gradient_, self.C_,
                             self.comp_norm_,
                             self.G_,
                             subset)
        else:
            self.update_BC(this_X, this_code, self.C_, self.B_,
                           self.gradient_, w, subset)
            self.update_dict(self.components_, self.gradient_, self.C_,
                             self.comp_norm_,
                             self.G_,
                             subset)
            self.update_B_full(this_X, this_code, self.B_, w)

    def partial_fit(self, X, sample_indices=None):
        """Compatibility with DictMF"""
        X = check_array(X, dtype='float', order='C')
        n_samples, n_features = X.shape
        batches = gen_batches(n_samples, self.batch_size)
        for batch in batches:
            len_batch = batch.stop - batch.start
            self.counter_ += len_batch
            self.col_counter_[sample_indices[batch]] += 1
            this_X = X[batch]
            this_G_average = self.G_average_[sample_indices[batch]]\
                if self.G_agg == 'average' else None
            this_Dx_average = self.Dx_average_[sample_indices[batch]]\
                if self.Dx_agg == 'average' else None
            this_code = self.code_[sample_indices[batch]]
            this_col_counter = self.col_counter_[sample_indices[batch]]

            self.batch_fit(this_Dx_average, this_G_average,
                           this_X, this_code, this_col_counter)
        if self.verbose:
            print('Sample %i' % self.counter_)
            if self.callback is not None:
                self.callback(self)

    def compute_code(self, this_X, components, G, G_average,
                     Dx_average, code,
                     w_sample, subset):
        batch_size, n_features = this_X.shape
        reduction = n_features / subset.shape[0]

        if self.Dx_agg != 'full' or self.G_agg != 'full':
            components_subset = components[:, subset]

        if self.Dx_agg == 'full':
            Dx = components.dot(this_X.T)
        else:
            X_subset = this_X[:, subset]
            Dx = X_subset.dot(components_subset.T) * reduction
            if self.Dx_agg == 'average':
                Dx_average *= 1 - w_sample[:, np.newaxis]
                Dx_average += Dx * w_sample[:, np.newaxis]
                Dx = Dx_average

        if self.G_agg != 'full':
            G = components_subset.dot(components_subset.T) * reduction
            if self.Dx_agg == 'average':
                G_average *= 1 - w_sample[:, np.newaxis, np.newaxis]
                # For some reason dot with 3d array makes
                # the product along the 2nd dimension of right side factor
                G_average += w_sample[:, np.newaxis].dot(G[:, np.newaxis, :])

        for i in range(batch_size):
            if self.G_agg == 'average':
                self.linear_regression(G_average[i], Dx[i],
                                       this_X[i], code[i])
            else:
                self.linear_regression(G, Dx[i], this_X[i], code[i])

    def update_BC(self, this_X, this_code, C, B, gradient, w, subset):
        batch_size, n_features = this_X.shape
        C *= (1 - w / batch_size)
        C += w * this_code.T.dot(this_code)

        if self.BC_agg == 'full':
            self.update_B_full(this_X, this_code, B, w)
            gradient[:, :] = B
        else:
            X_subset = this_X[:, subset]
            B_subset = B[:, subset]
            B_subset *= (1 - w / batch_size)
            B_subset += w * this_code.T.dot(X_subset)
            gradient[:, subset] = B_subset

    def update_B_full(self, this_X, this_code, B, w):
        batch_size, n_features = this_X.shape
        B *= (1 - w / batch_size)
        B += w * this_code.T.dot(this_X)

    def update_dict(self, components, gradient, C, norm, G, subset):
        len_subset = subset.shape[0]
        n_components, n_features = components.shape
        components_subset = components[:, subset]
        atom_temp = np.zeros(len_subset)
        gradient_subset = gradient[:, subset]
        ger, = scipy.linalg.get_blas_funcs(('ger',), (C, components_subset))

        if self.G_agg == 'full' and len_subset < n_features / 2.:
            G -= components_subset.dot(components_subset.T)

        gradient_subset -= C.dot(components_subset)

        order = self.random_state_.permutation(n_components)

        for k in order:
            subset_norm = enet_norm_fast(components_subset[k],
                                         self.comp_l1_ratio)
            norm[k] += subset_norm
            gradient_subset = ger(1.0, C[k], components_subset[k],
                                  a=gradient_subset, overwrite_a=True)
            if C[k, k] > 1e-20:
                components_subset[k] = gradient_subset[k] / self.C_[k, k]
                # Else do not update
            if self.comp_pos:
                components_subset[components_subset < 0] = 0
            enet_projection_fast(components_subset[k],
                                 atom_temp,
                                 norm[k], self.comp_l1_ratio)
            components_subset[k] = atom_temp
            subset_norm = enet_norm_fast(components_subset[k],
                                         self.comp_l1_ratio)
            norm[k] -= subset_norm
            gradient_subset = ger(-1.0,  C[k], components_subset[k],
                                  a=gradient_subset, overwrite_a=True)

        components[:, subset] = components_subset

        if self.G_agg == 'full':
            if len_subset < n_features / 2.:
                G += components_subset.dot(components_subset.T)
            else:
                G[:] = components.dot(components.T)

    def linear_regression(self, G, Dx, this_X, code):
        n_components = G.shape[0]
        if self.code_l1_ratio == 0:
            G.flat[::n_components + 1] += self.code_alpha
            code[:] = linalg.solve(G, Dx)
            G.flat[::n_components + 1] -= self.code_alpha
        else:
            # this_X is only used to scale tolerance
            cd_fast.enet_coordinate_descent_gram(
                code,
                self.code_alpha * self.code_l1_ratio,
                self.code_alpha * (1 - self.code_l1_ratio),
                G, Dx, this_X, 100, 1e-2, self.random_state_,
                False, self.code_pos)

    def transform(self, X):
        X = check_array(X, order='C', dtype='float')
        n_samples, n_features = X.shape
        if self.G_agg != 'full':
            G = self.components_.dot(self.components_.T)
        Dx = X.dot(self.components_.T)
        code = np.empty((n_samples, self.n_components))
        for i in range(n_samples):
            self.linear_regression(G, Dx[i], X[i], code[i])
        return code.T

    def score(self, X):
        code = self.transform(X)
        loss = np.sum((X - code.T.dot(self.components_)) ** 2) / 2
        norm1_code = np.sum(np.abs(code))
        norm2_code = np.sum(code ** 2)
        regul = self.code_alpha * (norm1_code * self.code_l1_ratio
                                   + (1 - self.code_l1_ratio) * norm2_code / 2)
        return (loss + regul) / X.shape[0]
