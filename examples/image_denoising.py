from time import time

import matplotlib.pyplot as plt
import numpy as np
from scipy import misc
from sklearn.feature_extraction.image import extract_patches_2d
from sklearn.utils import check_random_state

from modl.dict_fact import DictFact
from math import sqrt

class Callback(object):
    """Utility class for plotting RMSE"""

    def __init__(self, X_tr, n_threads=None):
        self.X_tr = X_tr
        # self.X_te = X_t
        self.obj = []
        self.times = []
        self.iter = []
        # self.R = []
        self.start_time = time()
        self.test_time = 0
        self.profile = []
        self.n_threads = n_threads

    def __call__(self, mf):
        test_time = time()
        self.obj.append(mf.score(self.X_tr, n_threads=self.n_threads))
        self.test_time += time() - test_time
        self.times.append(time() - self.start_time - self.test_time)
        self.profile.append(mf.time)
        self.iter.append(mf.total_counter)


def main():
    # Convert from uint8 representation with values between 0 and 255 to
    # a floating point representation with values between 0 and 1.
    face = misc.face(gray=True)
    face = face / 255

    # downsample for higher speed
    # face = face[::2, ::2] + face[1::2, ::2] + face[::2, 1::2] + face[1::2, 1::2]
    # face /= 4.0
    height, width = face.shape

    # Distort the right half of the image
    print('Distorting image...')
    distorted = face.copy()
    # distorted[:, width // 2:] += 0.075 * np.random.randn(height, width // 2)

    # Extract all reference patches from the left half of the image
    print('Extracting reference patches...')
    t0 = time()
    redundancy = 16
    tile = int(sqrt(redundancy))
    patch_size = (8, 8)
    data = extract_patches_2d(distorted[:, :width // 2], patch_size,
                              max_patches=4000, random_state=0)
    tiled_data = np.empty(
        (data.shape[0], data.shape[1] * tile, data.shape[2] * tile))
    for i in range(tile):
        for j in range(tile):
            tiled_data[:, i::tile, j::tile] = data
    data = tiled_data
    patch_size = (8 * tile, 8 * tile)
    data = data.reshape(data.shape[0], -1)
    data -= np.mean(data, axis=0)
    data /= np.std(data, axis=0)
    print('done in %.2fs.' % (time() - t0))
    random_state = check_random_state(0)
    random_state.shuffle(data)

    ###############################################################################
    # Learn the dictionary from reference patches

    print('Learning the dictionary...')

    cb = Callback(data, n_threads=2)
    n_samples = data.shape[0]
    dico = DictFact(n_components=100, alpha=1,
                    l1_ratio=0,
                    pen_l1_ratio=.9,
                    batch_size=30,
                    learning_rate=.9,
                    sample_learning_rate=None,
                    reduction=10,
                    verbose=2,
                    G_agg='full',
                    Dx_agg='masked',
                    AB_agg='full',
                    subset_sampling='random',
                    dict_reduction='follow',
                    callback=cb,
                    n_threads=3,
                    n_samples=n_samples,
                    tol=1e-2,
                    random_state=42,
                    n_epochs=5)
    # warmup = 0 # 3 * n_samples
    # t0 = time()
    # reduction = dico.reduction
    # dico.set_params(reduction=1)
    # warmup_epochs = warmup // n_samples
    # for _ in range(warmup_epochs):
    #     dico.partial_fit(data)
    # warmup_rem = warmup % n_samples
    # if warmup_rem != 0:
    #     dico.partial_fit(data[:warmup_rem], np.arange(warmup, dtype='i4'))
    #     dico.set_params(reduction=reduction)
    #     dico.partial_fit(data[warmup_rem:],
    #                      np.arange(warmup, n_samples, dtype='i4'))
    # else:
    #     dico.set_params(reduction=reduction)
    # for i in range(dico.n_epochs - warmup_epochs):
    #     dico.partial_fit(data)
    dico.fit(data)
    V = dico.components_
    dt = cb.times[-1] if dico.callback != None else time() - t0
    print('done in %.2fs., test time: %.2fs' % (dt, cb.test_time))

    plt.figure(figsize=(4.2, 4))
    for i, comp in enumerate(V[:100]):
        plt.subplot(10, 10, i + 1)
        plt.imshow(comp.reshape(patch_size), cmap=plt.cm.gray_r,
                   interpolation='nearest')
        plt.xticks(())
        plt.yticks(())
    plt.suptitle('Dictionary learned from face patches\n' +
                 'Train time %.1fs on %d patches' % (dt, len(data)),
                 fontsize=16)
    plt.subplots_adjust(0.08, 0.02, 0.92, 0.85, 0.08, 0.23)

    fig, axes = plt.subplots(2, 1, sharex=True)

    profile = np.array(cb.profile)
    iter = np.array(cb.iter)
    obj = np.array(cb.obj)
    average_time = np.zeros_like(profile)
    average_time[1:] = (profile[1:] - profile[:-1]) / (iter[1:] - iter[:-1])[:, np.newaxis]

    axes[0].plot(iter[1:], obj[1:], marker='o')
    axes[1].plot(iter[1:], average_time[1:], marker='o')
    axes[1].legend(['Dx time', 'G time', 'Code time', 'Agg time',
                    'BCD time', 'Total'])
    axes[1].set_ylabel('Average time')
    axes[1].set_yscale('Log')
    axes[0].set_ylabel('Function value')
    #
    plt.show()


if __name__ == '__main__':
    main()
