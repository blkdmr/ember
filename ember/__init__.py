# -*- coding: utf-8 -*-

import os
import json
import tqdm
import numpy as np
import pandas as pd
import multiprocessing
from .features import PEFeatureExtractor
from sklearn.model_selection import GridSearchCV
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import (roc_auc_score, make_scorer)


def raw_feature_iterator(file_paths):
    """
    Yield raw feature strings from the inputed file paths
    """
    for path in file_paths:
        with open(path, "r") as fin:
            for line in fin:
                yield line


def vectorize(irow, raw_features_string, X_path, y_path, extractor, nrows):
    """
    Vectorize a single sample of raw features and write to a large numpy file
    """
    raw_features = json.loads(raw_features_string)
    feature_vector = extractor.process_raw_features(raw_features)

    y = np.memmap(y_path, dtype=np.float32, mode="r+", shape=nrows)
    y[irow] = raw_features["label"]

    X = np.memmap(X_path, dtype=np.float32, mode="r+", shape=(nrows, extractor.dim))
    X[irow] = feature_vector


def vectorize_unpack(args):
    """
    Pass through function for unpacking vectorize arguments
    """
    return vectorize(*args)


def vectorize_subset(X_path, y_path, raw_feature_paths, extractor, nrows):
    """
    Vectorize a subset of data and write it to disk
    """
    # Create space on disk to write features to
    X = np.memmap(X_path, dtype=np.float32, mode="w+", shape=(nrows, extractor.dim))
    y = np.memmap(y_path, dtype=np.float32, mode="w+", shape=nrows)
    del X, y

    # Distribute the vectorization work
    pool = multiprocessing.Pool()
    argument_iterator = ((irow, raw_features_string, X_path, y_path, extractor, nrows)
                         for irow, raw_features_string in enumerate(raw_feature_iterator(raw_feature_paths)))
    for _ in tqdm.tqdm(pool.imap_unordered(vectorize_unpack, argument_iterator), total=nrows):
        pass


def create_vectorized_features(data_dir, feature_version=2):
    """
    Create feature vectors from raw features and write them to disk
    """
    extractor = PEFeatureExtractor(feature_version)

    print("Vectorizing training set")
    X_path = os.path.join(data_dir, "X_train.dat")
    y_path = os.path.join(data_dir, "y_train.dat")
    raw_feature_paths = [os.path.join(data_dir, "train_features_{}.jsonl".format(i)) for i in range(6)]
    nrows = sum([1 for fp in raw_feature_paths for line in open(fp)])
    vectorize_subset(X_path, y_path, raw_feature_paths, extractor, nrows)

    print("Vectorizing test set")
    X_path = os.path.join(data_dir, "X_test.dat")
    y_path = os.path.join(data_dir, "y_test.dat")
    raw_feature_paths = [os.path.join(data_dir, "test_features.jsonl")]
    nrows = sum([1 for fp in raw_feature_paths for line in open(fp)])
    vectorize_subset(X_path, y_path, raw_feature_paths, extractor, nrows)


def read_vectorized_features(data_dir, subset=None, feature_version=2):
    """
    Read vectorized features into memory mapped numpy arrays
    """
    if subset is not None and subset not in ["train", "test"]:
        return None

    extractor = PEFeatureExtractor(feature_version)
    ndim = extractor.dim
    X_train = None
    y_train = None
    X_test = None
    y_test = None

    if subset is None or subset == "train":
        X_train_path = os.path.join(data_dir, "X_train.dat")
        y_train_path = os.path.join(data_dir, "y_train.dat")
        y_train = np.memmap(y_train_path, dtype=np.float32, mode="r")
        N = y_train.shape[0]
        X_train = np.memmap(X_train_path, dtype=np.float32, mode="r", shape=(N, ndim))
        if subset == "train":
            return X_train, y_train

    if subset is None or subset == "test":
        X_test_path = os.path.join(data_dir, "X_test.dat")
        y_test_path = os.path.join(data_dir, "y_test.dat")
        y_test = np.memmap(y_test_path, dtype=np.float32, mode="r")
        N = y_test.shape[0]
        X_test = np.memmap(X_test_path, dtype=np.float32, mode="r", shape=(N, ndim))
        if subset == "test":
            return X_test, y_test

    return X_train, y_train, X_test, y_test


def read_metadata_record(raw_features_string):
    """
    Decode a raw features string and return the metadata fields
    """
    all_data = json.loads(raw_features_string)
    metadata_keys = {"sha256", "appeared", "label", "avclass"}
    return {k: all_data[k] for k in all_data.keys() & metadata_keys}


def create_metadata(data_dir):
    """
    Write metadata to a csv file and return its dataframe
    """
    pool = multiprocessing.Pool()

    train_feature_paths = [os.path.join(data_dir, "train_features_{}.jsonl".format(i)) for i in range(6)]
    train_records = list(pool.imap(read_metadata_record, raw_feature_iterator(train_feature_paths)))

    metadata_keys = ["sha256", "appeared", "label", "avclass"]
    ordered_metadata_keys = [k for k in metadata_keys if k in train_records[0].keys()]

    train_metadf = pd.DataFrame(train_records)[ordered_metadata_keys]
    train_metadf.to_csv(os.path.join(data_dir, "train_metadata.csv"))

    train_records = [dict(record, **{"subset": "train"}) for record in train_records]

    test_feature_paths = [os.path.join(data_dir, "test_features.jsonl")]
    test_records = list(pool.imap(read_metadata_record, raw_feature_iterator(test_feature_paths)))

    test_metadf = pd.DataFrame(test_records)[ordered_metadata_keys]
    test_metadf.to_csv(os.path.join(data_dir, "test_metadata.csv"))

    test_records = [dict(record, **{"subset": "test"}) for record in test_records]

    all_metadata_keys = ordered_metadata_keys + ["subset"]
    metadf = pd.DataFrame(train_records + test_records)[all_metadata_keys]
    metadf.to_csv(os.path.join(data_dir, "metadata.csv"))
    return metadf


def read_metadata(data_dir):
    """
    Read an already created metadata file and return its dataframe
    """
    return pd.read_csv(os.path.join(data_dir, "metadata.csv"), index_col=0)
