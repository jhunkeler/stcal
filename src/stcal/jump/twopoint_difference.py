import logging
import warnings

import numpy as np
import warnings
from astropy import stats

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)


class TwoPointParams:
    def __init__(self, jump_data=None, copy_arrs=False):
        if jump_data is not None:
            self.normal_rej_thresh = jump_data.rejection_thresh

            self.two_diff_rej_thresh = jump_data.three_grp_thresh
            self.three_diff_rej_thresh = jump_data.four_grp_thresh
            self.nframes = jump_data.nframes,
                
            self.flag_4_neighbors = jump_data.flag_4_neighbors
            self.max_jump_to_flag_neighbors = jump_data.max_jump_to_flag_neighbors
            self.min_jump_to_flag_neighbors = jump_data.min_jump_to_flag_neighbors

            self.fl_good = jump_data.fl_good
            self.fl_sat = jump_data.fl_sat
            self.fl_jump = jump_data.fl_jump
            self.fl_ngv = jump_data.fl_ngv
            self.fl_dnu = jump_data.fl_dnu
            self.fl_ref = jump_data.fl_ref

            self.after_jump_flag_e1=jump_data.after_jump_flag_e1
            self.after_jump_flag_n1=jump_data.after_jump_flag_n1
            self.after_jump_flag_e2=jump_data.after_jump_flag_e2
            self.after_jump_flag_n2=jump_data.after_jump_flag_n2

            self.minimum_groups = jump_data.minimum_groups
            self.minimum_sigclip_groups = jump_data.minimum_sigclip_groups
            self.only_use_ints = jump_data.only_use_ints
            self.min_diffs_single_pass = jump_data.min_diffs_single_pass
        else:
            self.normal_rej_thresh = None

            self.two_diff_rej_thresh = None
            self.three_diff_rej_thresh = None
            self.nframes = None
                
            self.flag_4_neighbors = None
            self.max_jump_to_flag_neighbors = None
            self.min_jump_to_flag_neighbors = None

            self.fl_good = None
            self.fl_sat = None
            self.fl_jump = None
            self.fl_ngv = None
            self.fl_dnu = None
            self.fl_ref = None

            self.after_jump_flag_e1 = None
            self.after_jump_flag_n1 = None
            self.after_jump_flag_e2 = None
            self.after_jump_flag_n2 = None

            self.minimum_groups = None
            self.minimum_sigclip_groups = None
            self.only_use_ints = None
            self.min_diffs_single_pass = None

        self.copy_arrs = copy_arrs


def find_crs(dataa, group_dq, read_noise, twopt_p):
    """
    Find CRs/Jumps in each integration within the input data array. The input
    data array is assumed to be in units of electrons, i.e. already multiplied
    by the gain. We also assume that the read noise is in units of electrons.
    We also assume that there are at least three groups in the integrations.
    This was checked by jump_step before this routine is called.

    Parameters
    ----------
    dataa: float, 4D array (num_ints, num_groups, num_rows,  num_cols)
        input ramp data

    group_dq : int, 4D array
        group DQ flags

    read_noise : float, 2D array
        The read noise of each pixel

    twopt_p : TwoPointParams
        Contains parameters to compute the two point difference jump detection.

    Returns
    -------
    gdq : int, 4D array
        group DQ array with reset flags

    row_below_gdq : int, 3D array (num_ints, num_groups, num_cols)
        pixels below current row also to be flagged as a CR

    row_above_gdq : int, 3D array (num_ints, num_groups, num_cols)
        pixels above current row also to be flagged as a CR

    num_primary_crs : int
        number of primary cosmic rays found

    dummy/stddev : float
        the standard deviation computed during sigma clipping

    """
    # START find_crs
    # copy data and group DQ array
    dat, gdq = possibly_copy(dataa, group_dq, twopt_p)

    # Get data characteristics
    nints, ngroups, nrows, ncols = dataa.shape
    ndiffs = (ngroups - 1) * nints

    # get readnoise, squared
    read_noise_2 = read_noise**2

    # create arrays for output
    row_above_gdq = np.zeros((nints, ngroups, ncols), dtype=np.uint8)
    row_below_gdq = np.zeros((nints, ngroups, ncols), dtype=np.uint8)

    total_groups, total_diffs, total_usable_diffs = compute_totals(
            nints, ngroups, gdq, twopt_p)

    if too_few_groups(nints, ngroups, total_groups, twopt_p):
        log.info("Jump Step was skipped because exposure has less than the minimum number of usable groups")
        dummy = np.zeros((dataa.shape[1] - 1, dataa.shape[2], dataa.shape[3]),
                         dtype=np.float32)
        return gdq, row_below_gdq, row_above_gdq, 0, dummy

    # ----------------------------------------------------------------
    # calculate the differences between adjacent groups (first diffs)
    # use mask on data, so the results will have sat/donotuse groups masked
    first_diffs = np.diff(dat, axis=1)

    # calc. the median of first_diffs for each pixel along the group axis
    first_diffs_masked = np.ma.masked_array(first_diffs, mask=np.isnan(first_diffs))
    median_diffs = np.ma.median(first_diffs_masked, axis=(0, 1))
    # calculate sigma for each pixel
    sigma = np.sqrt(np.abs(median_diffs) + read_noise_2 / twopt_p.nframes)

    # reset sigma so pxels with 0 readnoise are not flagged as jumps
    sigma[np.where(sigma == 0.)] = np.nan

    # compute 'ratio' for each group. this is the value that will be
    # compared to 'threshold' to classify jumps. subtract the median of
    # first_diffs from first_diffs, take the absolute value and divide by sigma.
    e_jump_4d = first_diffs - median_diffs[np.newaxis, :, :]
    ratio_all = np.abs(first_diffs - median_diffs[np.newaxis, np.newaxis, :, :]) / \
                sigma[np.newaxis, np.newaxis, :, :]
    # Test to see if there are enough groups to use sigma clipping
    
    if enough_sigclip_groups(nints, total_groups, twopt_p):
        # XXX None of the CI tests in STCAL, nor JWST get here
        log.info(" Jump Step using sigma clip {} greater than {}, rejection threshold {}".format(
            str(total_groups), str(twopt_p.minimum_sigclip_groups), str(twopt_p.normal_rej_thresh)))
        warnings.filterwarnings("ignore", ".*All-NaN slice encountered.*", RuntimeWarning)
        warnings.filterwarnings("ignore", ".*Mean of empty slice.*", RuntimeWarning)
        warnings.filterwarnings("ignore", ".*Degrees of freedom <= 0.*", RuntimeWarning)

        mean, median, stddev, clipped_diffs = run_sigma_clipping(first_diffs_masked, twopt_p)
        gdq = mask_gdq(nints, ngroups, clipped_diffs, first_diffs_masked, twopt_p)

        warnings.resetwarnings()
    else:  # There are not enough groups for sigma clipping

        # set 'saturated' or 'do not use' pixels to nan in data
        dat[np.where(np.bitwise_and(gdq, twopt_p.fl_sat))] = np.nan
        dat[np.where(np.bitwise_and(gdq, twopt_p.fl_dnu))] = np.nan

        # calculate the differences between adjacent groups (first diffs)
        # use mask on data, so the results will have sat/donotuse groups masked
        first_diffs = np.diff(dat, axis=1)

        if total_usable_diffs >= twopt_p.min_diffs_single_pass:
            
            warnings.filterwarnings("ignore", ".*All-NaN slice encountered.*", RuntimeWarning)
            median_diffs = np.nanmedian(first_diffs, axis=(0, 1))
            warnings.resetwarnings()

            # calculate sigma for each pixel
            sigma = np.sqrt(np.abs(median_diffs) + read_noise_2 / twopt_p.nframes)
            # reset sigma so pixels with 0 read noise are not flagged as jumps
            sigma[np.where(sigma == 0.)] = np.nan

            # compute 'ratio' for each group. this is the value that will be
            # compared to 'threshold' to classify jumps. subtract the median of
            # first_diffs from first_diffs, take the abs. value and divide by sigma.
            e_jump = first_diffs - median_diffs[np.newaxis, np.newaxis, :, :]

            ratio = np.abs(e_jump) / sigma[np.newaxis, np.newaxis, :, :]
            masked_ratio = np.ma.masked_greater(ratio, twopt_p.normal_rej_thresh)
            #  The jump mask is the ratio greater than the threshold and the difference is usable
            jump_mask = np.logical_and(masked_ratio.mask, np.logical_not(first_diffs_masked.mask))
            gdq[:, 1:, :, :] = np.bitwise_or(gdq[:, 1:, :, :], jump_mask *
                                             np.uint8(twopt_p.fl_jump))
        else:  # low number of diffs requires iterative flagging
            # calculate the differences between adjacent groups (first diffs)
            # use mask on data, so the results will have sat/donotuse groups masked
            first_diffs = np.abs(np.diff(dat, axis=1))

            # calc. the median of first_diffs for each pixel along the group axis
            median_diffs = calc_med_first_diffs(first_diffs)

            # calculate sigma for each pixel
            sigma = np.sqrt(np.abs(median_diffs) + read_noise_2 / twopt_p.nframes)
            # reset sigma so pxels with 0 readnoise are not flagged as jumps
            sigma[np.where(sigma == 0.0)] = np.nan

            # compute 'ratio' for each group. this is the value that will be
            # compared to 'threshold' to classify jumps. subtract the median of
            # first_diffs from first_diffs, take the abs. value and divide by sigma.
            e_jump = first_diffs - median_diffs[np.newaxis, :, :]
            ratio = np.abs(e_jump) / sigma[np.newaxis, :, :]

            # create a 2d array containing the value of the largest 'ratio' for each pixel
            warnings.filterwarnings("ignore", ".*All-NaN slice encountered.*", RuntimeWarning)
            max_ratio = np.nanmax(ratio, axis=1)
            warnings.resetwarnings()
            # now see if the largest ratio of all groups for each pixel exceeds the threshold.
            # there are different threshold for 4+, 3, and 2 usable groups
            num_unusable_groups = np.sum(np.isnan(first_diffs), axis=(0, 1))
            int4cr, row4cr, col4cr = np.where(
                np.logical_and(ndiffs - num_unusable_groups >= 4, max_ratio > twopt_p.normal_rej_thresh)
            )
            int3cr, row3cr, col3cr = np.where(
                np.logical_and(ndiffs - num_unusable_groups == 3, max_ratio > twopt_p.three_diff_rej_thresh)
            )
            int2cr, row2cr, col2cr = np.where(
                np.logical_and(ndiffs - num_unusable_groups == 2, max_ratio > twopt_p.two_diff_rej_thresh)
            )
            # get the rows, col pairs for all pixels with at least one CR
            # all_crs_int = np.concatenate((int4cr, int3cr, int2cr))
            all_crs_row = np.concatenate((row4cr, row3cr, row2cr))
            all_crs_col = np.concatenate((col4cr, col3cr, col2cr))

            # iterate over all groups of the pix w/ an initial CR to look for subsequent CRs
            # flag and clip the first CR found. recompute median/sigma/ratio
            # and repeat the above steps of comparing the max 'ratio' for each pixel
            # to the threshold to determine if another CR can be flagged and clipped.
            # repeat this process until no more CRs are found.
            for j in range(len(all_crs_row)):
                # get arrays of abs(diffs), ratio, readnoise for this pixel
                pix_first_diffs = first_diffs[:, :, all_crs_row[j], all_crs_col[j]]
                pix_ratio = ratio[:, :, all_crs_row[j], all_crs_col[j]]
                pix_rn2 = read_noise_2[all_crs_row[j], all_crs_col[j]]

                # Create a mask to flag CRs. pix_cr_mask = 0 denotes a CR
                pix_cr_mask = np.ones(pix_first_diffs.shape, dtype=bool)

                # set the largest ratio as a CR
                location = np.unravel_index(np.nanargmax(pix_ratio), pix_ratio.shape)
                pix_cr_mask[location] = 0
                new_CR_found = True

                # loop and check for more CRs, setting the mask as you go and
                # clipping the group with the CR. stop when no more CRs are found
                # or there is only one two diffs left (which means there is
                # actually one left, since the next CR will be masked after
                # checking that condition)
                while new_CR_found and (ndiffs - np.sum(np.isnan(pix_first_diffs)) > 2):
                    new_CR_found = False

                    # set CRs to nans in first diffs to clip them
                    pix_first_diffs[~pix_cr_mask] = np.nan

                    # recalculate median, sigma, and ratio
                    new_pix_median_diffs = calc_med_first_diffs(pix_first_diffs)

                    new_pix_sigma = np.sqrt(np.abs(new_pix_median_diffs) + pix_rn2 / twopt_p.nframes)
                    new_pix_ratio = np.abs(pix_first_diffs - new_pix_median_diffs) / new_pix_sigma

                    # check if largest ratio exceeds threshold appropriate for num remaining groups

                    # select appropriate thresh. based on number of remaining groups
                    rej_thresh = twopt_p.normal_rej_thresh
                    if ndiffs - np.sum(np.isnan(pix_first_diffs)) == 3:
                        rej_thresh = twopt_p.three_diff_rej_thresh
                    if ndiffs - np.sum(np.isnan(pix_first_diffs)) == 2:
                        rej_thresh = twopt_p.two_diff_rej_thresh
                    max_idx = np.nanargmax(new_pix_ratio)
                    location = np.unravel_index(max_idx, new_pix_ratio.shape)
                    if new_pix_ratio[location] > rej_thresh:
                        new_CR_found = True
                        pix_cr_mask[location] = 0
                    unusable_diffs = np.sum(np.isnan(pix_first_diffs))
                # Found all CRs for this pix - set flags in input DQ array
                gdq[:, 1:, all_crs_row[j], all_crs_col[j]] = np.bitwise_or(
                    gdq[:, 1:, all_crs_row[j], all_crs_col[j]],
                    twopt_p.fl_jump * np.invert(pix_cr_mask),
                )
    # ----------------------------------------------------------------

    cr_integ, cr_group, cr_row, cr_col = np.where(np.bitwise_and(gdq, twopt_p.fl_jump))
    num_primary_crs = len(cr_group)
    if twopt_p.flag_4_neighbors:  # iterate over each 'jump' pixel
        for j in range(len(cr_group)):
            ratio_this_pix = ratio_all[cr_integ[j], cr_group[j] - 1, cr_row[j], cr_col[j]]

            # Jumps must be in a certain range to have neighbors flagged
            if (ratio_this_pix < twopt_p.max_jump_to_flag_neighbors) and (
                ratio_this_pix > twopt_p.min_jump_to_flag_neighbors
            ):
                integ = cr_integ[j]
                group = cr_group[j]
                row = cr_row[j]
                col = cr_col[j]

                # This section saves flagged neighbors that are above or
                # below the current range of row. If this method
                # running in a single process, the row above and below are
                # not used. If it is running in multiprocessing mode, then
                # the rows above and below need to be returned to
                # find_jumps to use when it reconstructs the full group dq
                # array from the slices.

                # Only flag adjacent pixels if they do not already have the
                # 'SATURATION' or 'DONOTUSE' flag set
                if row != 0:
                    if (gdq[integ, group, row - 1, col] & twopt_p.fl_sat) == 0 and (
                        gdq[integ, group, row - 1, col] & twopt_p.fl_dnu
                    ) == 0:
                        gdq[integ, group, row - 1, col] = np.bitwise_or(
                            gdq[integ, group, row - 1, col], twopt_p.fl_jump
                        )
                else:
                    row_below_gdq[integ, cr_group[j], cr_col[j]] = twopt_p.fl_jump

                if row != nrows - 1:
                    if (gdq[integ, group, row + 1, col] & twopt_p.fl_sat) == 0 and (
                        gdq[integ, group, row + 1, col] & twopt_p.fl_dnu
                    ) == 0:
                        gdq[integ, group, row + 1, col] = np.bitwise_or(
                            gdq[integ, group, row + 1, col], twopt_p.fl_jump
                        )
                else:
                    row_above_gdq[integ, cr_group[j], cr_col[j]] = twopt_p.fl_jump

                # Here we are just checking that we don't flag neighbors of
                # jumps that are off the detector.
                if (
                    cr_col[j] != 0
                    and (gdq[integ, group, row, col - 1] & twopt_p.fl_sat) == 0
                    and (gdq[integ, group, row, col - 1] & twopt_p.fl_dnu) == 0
                ):
                    gdq[integ, group, row, col - 1] = np.bitwise_or(
                        gdq[integ, group, row, col - 1], twopt_p.fl_jump
                    )

                if (
                    cr_col[j] != ncols - 1
                    and (gdq[integ, group, row, col + 1] & twopt_p.fl_sat) == 0
                    and (gdq[integ, group, row, col + 1] & twopt_p.fl_dnu) == 0
                ):
                    gdq[integ, group, row, col + 1] = np.bitwise_or(
                        gdq[integ, group, row, col + 1], twopt_p.fl_jump
                    )

    # flag n groups after jumps above the specified thresholds to account for
    # the transient seen after ramp jumps
    flag_e_threshold = [twopt_p.after_jump_flag_e1, twopt_p.after_jump_flag_e2]
    flag_groups = [twopt_p.after_jump_flag_n1, twopt_p.after_jump_flag_n2]
    for cthres, cgroup in zip(flag_e_threshold, flag_groups):
        if cgroup > 0:
            cr_intg, cr_group, cr_row, cr_col = np.where(np.bitwise_and(gdq, twopt_p.fl_jump))
            for j in range(len(cr_group)):
                intg = cr_intg[j]
                group = cr_group[j]
                row = cr_row[j]
                col = cr_col[j]
                if e_jump_4d[intg, group - 1, row, col] >= cthres:
                    for kk in range(group, min(group + cgroup + 1, ngroups)):
                        if (gdq[intg, kk, row, col] & twopt_p.fl_sat) == 0 and (
                            gdq[intg, kk, row, col] & twopt_p.fl_dnu
                        ) == 0:
                            gdq[intg, kk, row, col] = np.bitwise_or(
                                    gdq[intg, kk, row, col], twopt_p.fl_jump)

    if "stddev" in locals():
        return gdq, row_below_gdq, row_above_gdq, num_primary_crs, stddev

    if twopt_p.only_use_ints:
        dummy = np.zeros((dataa.shape[1] - 1, dataa.shape[2], dataa.shape[3]), dtype=np.float32)
    else:
        dummy = np.zeros((dataa.shape[2], dataa.shape[3]), dtype=np.float32)

    return gdq, row_below_gdq, row_above_gdq, num_primary_crs, dummy
# END find_crs


def mask_gdq(nints, ngroups, clipped_diffs, first_diffs_masked, twopt_p):
    jump_mask = np.logical_and(clipped_diffs.mask, np.logical_not(first_diffs_masked.mask))
    jump_mask[np.bitwise_and(jump_mask, gdq[:, 1:, :, :] == twopt_p.fl_sat)] = False
    jump_mask[np.bitwise_and(jump_mask, gdq[:, 1:, :, :] == twopt_p.fl_dnu)] = False
    jump_mask[np.bitwise_and(jump_mask, gdq[:, 1:, :, :] == (twopt_p.fl_dnu + twopt_p.fl_sat))] = False

    gdq[:, 1:, :, :] = np.bitwise_or(
            gdq[:, 1:, :, :], jump_mask * np.uint8(twopt_p.fl_jump))
    # if grp is all jump set to do not use
    for integ in range(nints):
        for grp in range(ngroups):
            if np.all(np.bitwise_or(np.bitwise_and(gdq[integ, grp, :, :], twopt_p.fl_jump),
                                    np.bitwise_and(gdq[integ, grp, :, :], twopt_p.fl_dnu))):
                jumpy, jumpx = np.where(gdq[integ, grp, :, :] == twopt_p.fl_jump)
                gdq[integ, grp, jumpy, jumpx] = 0

    return gdq


def run_sigma_clipping(first_diffs_masked, twopt_p):
    if twopt_p.only_use_ints:
        mean, median, stddev = stats.sigma_clipped_stats(
            first_diffs_masked, sigma=twopt_p.normal_rej_thresh, axis=0)
        clipped_diffs = stats.sigma_clip(
            first_diffs_masked, sigma=twopt_p.normal_rej_thresh, axis=0, masked=True)
    else:
        mean, median, stddev = stats.sigma_clipped_stats(
            first_diffs_masked, sigma=twopt_p.normal_rej_thresh, axis=(0, 1))
        clipped_diffs = stats.sigma_clip(
            first_diffs_masked, sigma=twopt_p.normal_rej_thresh, axis=(0, 1), masked=True)
    return mean, median, stddev, clipped_diffs


def compute_totals(nints, ngroups, gdq, twopt_p):
    num_flagged_grps = compute_nflagged_groups(nints, ngroups, gdq, twopt_p)

    total_groups = nints * (ngroups - num_flagged_grps)
    total_diffs = nints * (ngroups - 1 - num_flagged_grps)
    total_usable_diffs = total_diffs - num_flagged_grps
    return total_groups, total_diffs, total_usable_diffs


def enough_sigclip_groups(nints, total_groups, twopt_p):
    test1 = twopt_p.only_use_ints and nints >= twopt_p.minimum_sigclip_groups
    test2 = not twopt_p.only_use_ints and total_groups >= twopt_p.minimum_sigclip_groups
    return test1 or test2


def compute_nflagged_groups(nints, ngroups, gdq, twopt_p):
    # determine the number of groups with all pixels set to DO_NOT_USE
    num_flagged_grps = 0
    for integ in range(nints):
        for grp in range(ngroups):
            if np.all(np.bitwise_and(gdq[integ, grp, :, :], twopt_p.fl_dnu)):
                num_flagged_grps += 1

    return num_flagged_grps

def nan_invalid_data(dat, gdq, twopt_p):
    dat[np.where(np.bitwise_and(gdq, twopt_p.fl_sat))] = np.nan
    dat[np.where(np.bitwise_and(gdq, twopt_p.fl_dnu))] = np.nan
    dat[np.where(np.bitwise_and(gdq, twopt_p.fl_dnu + twopt_p.fl_sat))] = np.nan
    return dat


def possibly_copy(dataa, group_dq, twopt_p):
    if twopt_p.copy_arrs:
        dat = dataa.copy()
        gdq = group_dq.copy()
    else:
        dat = dataa
        gdq = group_dq
    dat = nan_invalid_data(dat, gdq, twopt_p)
    return dat, gdq


def too_few_groups(nints, ngrps, total_groups, twopt_p):
    test1 = (
        ngrps < twopt_p.minimum_groups
        and twopt_p.only_use_ints
        and nints < twopt_p.minimum_sigclip_groups
    )
    test2 = (
        not twopt_p.only_use_ints
        and nints * ngrps < twopt_p.minimum_sigclip_groups
        and total_groups < twopt_p.minimum_groups
    )
    return test1 or test2


def calc_med_first_diffs(in_first_diffs):
    """Calculate the median of `first diffs` along the group axis.

    If there are 4+ usable groups (e.g not flagged as saturated, donotuse,
    or a previously clipped CR), then the group with largest absolute
    first difference will be clipped and the median of the remaining groups
    will be returned. If there are exactly 3 usable groups, the median of
    those three groups will be returned without any clipping. Finally, if
    there are two usable groups, the group with the smallest absolute
    difference will be returned.
    Parameters
    ----------
    in_first_diffs : array, float
        array containing the first differences of adjacent groups
        for a single integration. Can be 3d or 1d (for a single pix)

    Returns
    -------
    median_diffs : float or array, float
        If the input is a single pixel, a float containing the median for
        the groups in that pixel will be returned. If the input is a 3d
        array of several pixels, a 2d array with the median for each pixel
        will be returned.
    """
    first_diffs = in_first_diffs.copy()
    if first_diffs.ndim == 1:  # in the case where input is a single pixel
        return calc_med_first_diffs_dim1(in_first_diffs, first_diffs)
    elif first_diffs.ndim == 2:  # in the case where input is a single pixel
        return calc_med_first_diffs_dim2(in_first_diffs, first_diffs)
    elif first_diffs.ndim == 4:
        return calc_med_first_diffs_dim4(in_first_diffs, first_diffs)


def calc_med_first_diffs_dim1(in_first_diffs, first_diffs):
    num_usable_groups = len(first_diffs) - np.sum(np.isnan(first_diffs), axis=0)
    if num_usable_groups >= 4:  # if 4+, clip largest and return median
        mask = np.ones_like(first_diffs).astype(bool)
        mask[np.nanargmax(np.abs(first_diffs))] = False  # clip the diff with the largest abs value
        return np.nanmedian(first_diffs[mask])

    if num_usable_groups == 3:  # if 3, no clipping just return median
        return np.nanmedian(first_diffs)

    if num_usable_groups == 2:  # if 2, return diff with minimum abs
        return first_diffs[np.nanargmin(np.abs(first_diffs))]

    return np.nan


def calc_med_first_diffs_dim2(in_first_diffs, first_diffs):
    nansum = np.sum(np.isnan(first_diffs), axis=(0, 1))
    num_usable_diffs = first_diffs.size - np.sum(np.isnan(first_diffs), axis=(0, 1))
    if num_usable_diffs >= 4:  # if 4+, clip largest and return median
        mask = np.ones_like(first_diffs).astype(bool)
        location = np.unravel_index(first_diffs.argmax(), first_diffs.shape)
        mask[location] = False  # clip the diff with the largest abs value
        return np.nanmedian(first_diffs[mask])
    elif num_usable_diffs == 3:  # if 3, no clipping just return median
        return np.nanmedian(first_diffs)
    elif num_usable_diffs == 2:  # if 2, return diff with minimum abs
        TEST = np.nanargmin(np.abs(first_diffs))
        diff_min_idx = np.nanargmin(first_diffs)
        location = np.unravel_index(diff_min_idx, first_diffs.shape)
        return first_diffs[location]
    return np.nan


def calc_med_first_diffs_dim4(in_first_diffs, first_diffs):
    # if input is multi-dimensional
    nints, ndiffs, nrows, ncols = first_diffs.shape
    shaped_diffs = np.reshape(first_diffs, ((nints * ndiffs), nrows, ncols))
    num_usable_diffs = (ndiffs * nints) - np.sum(np.isnan(shaped_diffs), axis=0)
    median_diffs = np.zeros((nrows, ncols))  # empty array to store median for each pix

    # process groups with >=4 usable diffs
    row4, col4 = np.where(num_usable_diffs >= 4)  # locations of >= 4 usable diffs pixels
    if len(row4) > 0:
        four_slice = shaped_diffs[:, row4, col4]
        loc0 = np.nanargmax(four_slice, axis=0)
        shaped_diffs[loc0, row4, col4] = np.nan
        median_diffs[row4, col4] = np.nanmedian(shaped_diffs[:, row4, col4], axis=0)

    # process groups with 3 usable groups
    row3, col3 = np.where(num_usable_diffs == 3)  # locations of == 3 usable diff pixels
    if len(row3) > 0:
        three_slice = shaped_diffs[:, row3, col3]
        median_diffs[row3, col3] = np.nanmedian(three_slice, axis=0)  # add median to return arr for these pix

    # process groups with 2 usable groups
    row2, col2 = np.where(num_usable_diffs == 2)  # locations of == 2 usable diff pixels
    if len(row2) > 0:
        two_slice = shaped_diffs[ :, row2, col2]
        two_slice[np.nanargmax(np.abs(two_slice), axis=0),
                  np.arange(two_slice.shape[1])] = np.nan  # mask larger abs. val
        median_diffs[row2, col2] = np.nanmin(two_slice, axis=0)  # add med. to return arr

    # set the medians all groups with less than 2 usable diffs to nan to skip further
    # calculations for these pixels
    row_none, col_none = np.where(num_usable_diffs < 2)
    median_diffs[row_none, col_none] = np.nan

    return median_diffs
    
