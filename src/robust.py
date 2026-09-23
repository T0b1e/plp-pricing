"""Shared MAD (median absolute deviation) helpers for flagging price outliers.

Used both by src/fit_model.py (trimmed least squares) and app.py's price
variability diagnostic, so the two agree on what counts as an outlier.
"""
import numpy as np


def mad_scale(resid: np.ndarray, y: np.ndarray, floor_frac: float = 0.10, min_scale: float = 1.0) -> float:
    """Robust spread of resid, floored so flat-rate groups (MAD ~ 0) don't reject everything."""
    mad = np.median(np.abs(resid - np.median(resid))) * 1.4826
    return max(mad, floor_frac * np.median(np.abs(y)), min_scale)


def mad_outlier_mask(resid: np.ndarray, y: np.ndarray, k: float = 3.0) -> np.ndarray:
    """True where |resid| exceeds k times the floored MAD-based scale."""
    return np.abs(resid) > k * mad_scale(resid, y)
