import sys
from os import path
from os.path import join

import numpy as np
from modl.datasets import get_data_dirs
from sacred import Experiment
from sacred.observers import MongoObserver
from sacred.optional import pymongo
from sklearn.externals.joblib import Parallel
from sklearn.externals.joblib import delayed
from sklearn.model_selection import ParameterGrid
from sklearn.utils import check_random_state

sys.path.append(path.dirname(path.dirname
                             (path.dirname(path.abspath(__file__)))))

from examples.contrast.old.predict_contrast import predict_contrast

multi_predict_task = Experiment('multi_predict_contrast',
                                ingredients=[predict_contrast])
collection = multi_predict_task.path
observer = MongoObserver.create(db_name='amensch', collection=collection)
multi_predict_task.observers.append(observer)


@multi_predict_task.config
def config():
    n_jobs = 27
    dropout_latent_list = [0., 0.3, 0.6, 0.9]
    latent_dim_list = [None, 30, 100, 200]
    alpha_list = [1e-4]
    beta_list = [0]
    fine_tune_list = [0, 0.1]
    activation_list = ['linear']
    optimizer_list = ['adam']
    n_seeds = 5
    verbose = 0
    seed = 2


def single_run(config_updates, _id, master_id):
    observer = MongoObserver.create(db_name='amensch',
                                    collection=collection)
    predict_contrast.observers = [observer]

    @predict_contrast.config
    def config():
        n_jobs = 1
        factored = True
        loadings_dir = join(get_data_dirs()[0], 'pipeline', 'contrast',
                            'reduced')
        dropout_input = 0.25
        model_indexing = 'dataset'
        verbose = 0
        early_stop = False
        max_samples = int(1e7)

    run = predict_contrast._create_run(config_updates=config_updates)
    run._id = _id
    run.info['multi_predict_contrast_id'] = master_id
    run()


@multi_predict_task.automain
def run(dropout_latent_list,
        alpha_list,
        beta_list,
        activation_list,
        latent_dim_list,
        fine_tune_list,
        optimizer_list,
        n_seeds, n_jobs, _run, _seed):
    seed_list = check_random_state(_seed).randint(np.iinfo(np.uint32).max,
                                                  size=n_seeds)
    param_grid = ParameterGrid(
        {'datasets': [['brainomics', 'hcp']],
         'dataset_weight': [dict(hcp=i, archi=1, brainomics=1)
                            for i in [0, 0.5, 1]],
         'dropout_latent': dropout_latent_list,
         'latent_dim': latent_dim_list,
         'optimizer': optimizer_list,
         'alpha': alpha_list,
         'beta': beta_list,
         'activation': activation_list,
         'fine_tune': fine_tune_list,
         'seed': seed_list})

    # Robust labelling of experiments
    client = pymongo.MongoClient()
    database = client['amensch']
    c = database[collection].find({}, {'_id': 1})
    c = c.sort('_id', pymongo.DESCENDING).limit(1)
    c = c.next()['_id'] + 1 if c.count() else 1

    Parallel(n_jobs=n_jobs,
             verbose=10)(delayed(single_run)(config_updates, c + i, _run._id)
                         for i, config_updates in enumerate(param_grid))