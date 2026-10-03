"""PyTorch and TensorFlow/Keras networks as research candidates (docs/MODEL_SELECTION.md §22, docs/CANDIDATE_POOL.md).

Recorded at the user's request to accumulate a prospective record; deep-v1 found no gain over existing members, so these
never vote and are in no sealed pool or tracking registry. They are FITTED only where torch/tensorflow are installed (the
research environment, at the pinned versions) and stored as plain NumPy weights, so the live release loads and applies an
artifact without either library. With no artifact and no libraries a candidate is skipped with that reason, never replaced.

Kept out of learners.py / engine.py on purpose: those files are part of every existing candidate's identity."""
import hashlib
import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import numpy as np

from . import learners
from .features import FEATURE_SETS

FAMILIES = {"torch_mlp": "torch", "keras_mlp": "keras"}        # registry family -> deep_study library
PINNED = {"torch": "2.14.1", "tensorflow": "2.21.0"}           # the versions the networks are fitted with (extra `deep`)
LIBRARY_PACKAGE = {"torch": "torch", "keras": "tensorflow"}


class DependencyMissing(RuntimeError):
    pass


def is_deep(spec) -> bool:
    return spec.family in FAMILIES


def libraries_present(spec=None) -> bool:
    """True when the library this candidate is fitted with (or, without a spec, both) is installed at its pinned version."""
    need = [LIBRARY_PACKAGE[FAMILIES[spec.family]]] if spec is not None else list(PINNED)
    try:
        return all(version(p) == PINNED[p] for p in need)
    except PackageNotFoundError:
        return False


def identity() -> bytes:
    """What a deep candidate's definition adds to the registry's: the pinned libraries, the fixed settings and this code."""
    from .. import deep_study
    h = hashlib.sha256(json.dumps([PINNED, deep_study.SETTINGS, list(deep_study.SEEDS)], sort_keys=True).encode())
    for path in (Path(__file__), Path(deep_study.__file__)):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.digest()


class NumpyMLP:
    """A fitted ReLU network as plain arrays: picklable, deterministic, and applied without torch or tensorflow.
    kind 'class' (sigmoid), 'reg' (output rescaled to the target's units) or 'poisson' (exponential)."""
    classes_ = [0, 1]

    def __init__(self, layers: list, kind: str, shift: float = 0.0, scale: float = 1.0):
        self.layers = [(np.asarray(W, dtype=np.float64), np.asarray(b, dtype=np.float64)) for W, b in layers]
        self.kind, self.shift, self.scale = kind, float(shift), float(scale)

    def _raw(self, Z):
        a = np.asarray(Z, dtype=np.float64)
        for i, (W, b) in enumerate(self.layers):
            a = a @ W + b
            if i < len(self.layers) - 1:
                a = np.maximum(a, 0.0)
        return a.reshape(-1)

    def predict_proba(self, Z):
        p = 1.0 / (1.0 + np.exp(-self._raw(Z)))
        return np.column_stack([1 - p, p])

    def predict(self, Z):
        raw = self._raw(Z)
        return np.exp(raw) if self.kind == "poisson" else raw * self.scale + self.shift


def to_numpy(net) -> NumpyMLP:
    """The fitted TorchNet / KerasNet as arrays (weights as input-by-output matrices; dropout is inactive at prediction)."""
    from .. import deep_study
    if isinstance(net, deep_study.TorchNet):
        state = net.net.state_dict()
        keys = sorted({k.rsplit(".", 1)[0] for k in state}, key=int)
        layers = [(state[f"{k}.weight"].numpy().T, state[f"{k}.bias"].numpy()) for k in keys]
        return NumpyMLP(layers, net.target, net.shift, net.scale)
    weights = net.net.get_weights()
    return NumpyMLP([(weights[i], weights[i + 1]) for i in range(0, len(weights), 2)], "class")


def fit(spec, train: list) -> learners.Fitted:
    """The season fit of a deep candidate (same rule as deep-v1: epochs chosen on the latest earlier season, then a refit
    on every training row), exported to NumPy. Raises DependencyMissing where the pinned library is not installed."""
    if not libraries_present(spec):
        raise DependencyMissing(f"{spec.name} is fitted in the research environment only "
                                f"({LIBRARY_PACKAGE[FAMILIES[spec.family]]}=={PINNED[LIBRARY_PACKAGE[FAMILIES[spec.family]]]} not installed here)")
    from .. import deep_study
    model = deep_study.fit(FAMILIES[spec.family], spec.head, train, FEATURE_SETS[spec.features])
    if model is None:
        raise ValueError(f"{spec.name}: no earlier validation season to choose the number of epochs")
    if spec.head == "binary":
        objects = [to_numpy(n) for n in model.objects]
    elif spec.head == "normal":
        objects = [[to_numpy(n) for n in group] for group in model.objects]
    else:
        raise ValueError(f"{spec.name}: head {spec.head} is not registered for deep candidates")
    return learners.Fitted(model.kind, model.mu, model.sd, objects, model.info)


def residuals(spec, rows: list) -> list:
    """(season, margin error, total error) of the candidate's own out-of-sample predictions on earlier seasons, for the
    margin/total spread (as engine.walk gives for every other normal-head candidate)."""
    from .. import deep_study
    return deep_study.deep_walk(spec.sport, spec.name, FAMILIES[spec.family], spec.head, rows, log=lambda *_: None)["residuals"]
