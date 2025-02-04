""" Test various utility functions """
from numpy.testing import assert_array_equal
import numpy as np
import pytest

from stcal.resample.utils import (
    build_mask,
    bytes2human,
    compute_mean_pixel_area,
    get_tmeasure,
    is_imaging_wcs,
    resample_range,
)

from . helpers import JWST_DQ_FLAG_DEF


DQ = np.array([0, 1, 2, 3, 4, 5, 6, 7, 8])
BITVALUES = 2**0 + 2**2
BITVALUES_STR = f'{2**0}, {2**2}'
BITVALUES_INV_STR = f'~{2**0}, {2**2}'
JWST_NAMES = 'DO_NOT_USE,JUMP_DET'
JWST_NAMES_INV = '~' + JWST_NAMES


@pytest.mark.parametrize(
    'dq, bitvalues, expected', [
        (DQ, 0, np.array([1, 0, 0, 0, 0, 0, 0, 0, 0])),
        (DQ, BITVALUES, np.array([1, 1, 0, 0, 1, 1, 0, 0, 0])),
        (DQ, BITVALUES_STR, np.array([1, 1, 0, 0, 1, 1, 0, 0, 0])),
        (DQ, BITVALUES_INV_STR, np.array([1, 0, 1, 0, 0, 0, 0, 0, 1])),
        (DQ, JWST_NAMES, np.array([1, 1, 0, 0, 1, 1, 0, 0, 0])),
        (DQ, JWST_NAMES_INV, np.array([1, 0, 1, 0, 0, 0, 0, 0, 1])),
        (DQ, None, np.array([1, 1, 1, 1, 1, 1, 1, 1, 1])),
    ]
)
def test_build_mask(dq, bitvalues, expected):
    """ Test logic of mask building

    Parameters
    ----------
    dq: numpy.array
        The input data quality array

    bitvalues: int or str
        The bitvalues to match against

    expected: numpy.array
        Expected mask array
    """
    result = build_mask(dq, bitvalues, flag_name_map=JWST_DQ_FLAG_DEF)
    assert_array_equal(result, expected)


@pytest.mark.parametrize(
    "data_shape, bbox, exception, truth",
    [
        ((1, 2, 3), ((1, 500), (0, 350)), True, None),
        ((1, 2, 3), None, True, None),
        ((1, ), ((1, 500), (0, 350)), True, None),
        ((1, ), None, True, None),
        ((1000, 800), ((1, 500), ), True, None),
        ((1000, 800), ((1, 500), (0, 350), (0, 350)), True, None),
        ((1, ), ((1, 500), (0, 350)), True, None),
        ((1200, 1400), ((700, 300), (600, 800)), False, (700, 700, 600, 800)),
        ((1200, 1400), ((600, 800), (700, 300)), False, (600, 800, 700, 700)),
        ((1200, 1400), ((300, 700), (600, 800)), False, (300, 700, 600, 800)),
        ((750, 470), ((300, 700), (600, 800)), False, (300, 469, 600, 749)),
        ((750, 470), ((-5, -1), (-800, -600)), False, (0, 0, 0, 0)),
        ((750, 470), None, False, (0, 469, 0, 749)),
        ((-750, -470), None, False, (0, 0, 0, 0)),
    ]
)
def test_resample_range(data_shape, bbox, exception, truth):
    if exception:
        with pytest.raises(ValueError):
            resample_range(data_shape, bbox)
        return

    xyminmax = resample_range(data_shape, bbox)
    assert np.allclose(xyminmax, truth, rtol=0, atol=1e-12)


def test_get_tmeasure():
    model = {
        "measurement_time": 12.34,
        "exposure_time": 23.45,
    }

    assert get_tmeasure(model) == (12.34, True)

    model["measurement_time"] = None
    assert get_tmeasure(model) == (23.45, False)

    del model["measurement_time"]
    assert get_tmeasure(model) == (23.45, False)

    del model["exposure_time"]
    with pytest.raises(KeyError):
        get_tmeasure(model)


@pytest.mark.parametrize(
        "n, readable",
        [
            (10000, "9.8K"),
            (100001221, "95.4M")
        ]
)
def test_bytes2human(n, readable):
    assert bytes2human(n) == readable


def test_is_imaging_wcs(wcs_gwcs):
    assert is_imaging_wcs(wcs_gwcs)


def test_compute_mean_pixel_area(wcs_gwcs):
    area = np.deg2rad(wcs_gwcs.pixel_scale)**2
    assert abs(
        compute_mean_pixel_area(wcs_gwcs) / area - 1.0
    ) < 1e-5
