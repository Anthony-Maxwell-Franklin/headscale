#!/usr/bin/env python3
"""Post-pilot calendar support and conditional-association diagnostics.

The study has one project-wide exposure history. Creation-quarter strata compare
elapsed-duration risk sets within submission cohorts; they do not control every
calendar change occurring while a PR waits. No causal identification is claimed.
"""
import argparse
import hashlib
import json
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from patsy import dmatrix
from scipy import stats
from statsmodels.duration.hazard_regression import PHReg
from statsmodels.tools.sm_exceptions import ConvergenceWarning, HessianInversionWarning

from fit_piecewise_models import make_intervals


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "Paper/results/calendar-identifiability"
HELPERS = ("audit_calendar_identifiability.py", "fit_maintenance_models.py",
           "fit_piecewise_models.py")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def variance_decomposition(frame):
    """PR-weighted algebraic partition, without treating PRs as independent days."""
    values = frame["lag90_merger_hhi"].to_numpy(dtype=float)
    if not len(values) or not np.isfinite(values).all():
        raise ValueError("Defined, finite HHI observations are required")
    means = frame.groupby("creation_quarter")["lag90_merger_hhi"].transform("mean").to_numpy()
    total = float(np.sum((values - values.mean()) ** 2))
    within = float(np.sum((values - means) ** 2))
    between = float(np.sum((means - values.mean()) ** 2))
    if not np.isclose(total, within + between, rtol=1e-12, atol=1e-12):
        raise ValueError("Quarter variance decomposition does not conserve sums of squares")
    return dict(pr_weighting="One weight per eligible PR, not per independent date or exposure draw",
                total_sum_squares=total, within_quarter_sum_squares=within,
                between_quarter_sum_squares=between,
                within_quarter_fraction=within / total if total else None,
                between_quarter_fraction=between / total if total else None)


def calendar_basis(frame):
    """Four centered natural-cubic columns, fixed before quarter-stratified fits."""
    time = frame["calendar_years"].to_numpy(dtype=float)
    knots = np.quantile(time, [0, .25, .5, .75, 1])
    if len(np.unique(knots)) != 5:
        raise ValueError("Five distinct planned calendar knots are required")
    basis = dmatrix("cr(time, knots=interior, lower_bound=lower, upper_bound=upper, "
                    "constraints='center') - 1",
                    dict(time=time, interior=knots[1:-1], lower=knots[0], upper=knots[-1]))
    values = np.asarray(basis)
    if values.shape != (len(frame), 4) or np.linalg.matrix_rank(values) != 4:
        raise ValueError("Planned four-dimensional calendar basis is not identified")
    return values, dict(type="Centered natural cubic regression spline", degrees_of_freedom=4,
                        knot_quantiles=[0, .25, .5, .75, 1], knots_calendar_years=knots.tolist(),
                        formula=basis.design_info.describe(),
                        exposure_only_construction="Knots and centering use submission times, never outcomes")


def informative_rows(frame):
    """Keep intervals entering at least one merge-event risk set in their stratum."""
    retained = np.zeros(len(frame), dtype=bool)
    for _, group in frame.groupby("_stratum", sort=False):
        events = np.sort(group.loc[group["_event"] == 1, "_exit"].unique())
        if not len(events):
            continue
        positions = np.searchsorted(events, group["_entry"].to_numpy(), side="left")
        possible = positions < len(events)
        eligible = np.zeros(len(group), dtype=bool)
        eligible[possible] = events[positions[possible]] <= group["_exit"].to_numpy()[possible]
        retained[group.index.to_numpy()] = eligible
    return retained


def fit_diagnostic(frame, kind, calendar):
    """Record identified fits and honest failures under the fixed diagnostic plan."""
    data = frame.copy().reset_index(drop=True)
    data["hhi_per_01"] = data["lag90_merger_hhi"] * 10
    for source, target in [("lag90_merges", "merges_log2"),
                           ("backlog_at_submission", "backlog_log2"),
                           ("prior_author_prs", "prior_author_prs_log2")]:
        data[target] = np.log2(1 + data[source])
    experience = np.where(data["prior_author_prs"] == 0, "new",
                          np.where(data["prior_author_prs"] <= 4, "returning", "established"))
    data["_stratum"] = "all" if kind == "primary" else data["prior_integrator_author"].astype(str) + "-" + experience
    columns = ["hhi_per_01", "merges_log2", "backlog_log2"]
    if kind == "primary":
        columns.append("prior_author_prs_log2")
        if data["prior_integrator_author"].nunique() > 1:
            columns.append("prior_integrator_author")
    if calendar == "linear":
        columns.append("calendar_years")
    elif calendar == "spline4":
        spline, _ = calendar_basis(data)
        for index in range(4):
            column = "calendar_spline_" + str(index + 1)
            data[column] = spline[:, index]
            columns.append(column)
    elif calendar == "quarter_baseline":
        data["_stratum"] = data["creation_quarter"] + "|" + data["_stratum"]
    else:
        raise ValueError("Unknown planned calendar adjustment")
    if kind == "piecewise":
        columns.insert(1, "late_hhi_per_01")
        intervals = make_intervals(data, 30)
    elif kind == "primary":
        intervals = data.assign(_subject=np.arange(len(data)), _entry=0.,
                                _exit=data["duration_days"].clip(lower=1 / 86400),
                                _event=data["merge_event"])
    else:
        raise ValueError("Unknown model kind")
    retained = informative_rows(intervals)
    selected = intervals.loc[retained].copy()
    counts = dict(eligible_subjects=len(data), retained_subjects=int(selected["_subject"].nunique()),
                  interval_rows_before_filter=len(intervals), retained_interval_rows=len(selected),
                  no_event_risk_intervals_excluded=int((~retained).sum()),
                  merge_events=int(selected["_event"].sum()),
                  eligible_strata=int(intervals["_stratum"].nunique()),
                  informative_strata=int(selected["_stratum"].nunique()),
                  informative_quarter_clusters=int(selected["creation_quarter"].nunique()),
                  late_merge_events=int(selected.loc[selected["_entry"] > 0, "_event"].sum()) if kind == "piecewise" else None,
                  strata_with_late_merge_events=int(selected.loc[(selected["_entry"] > 0) & (selected["_event"] == 1), "_stratum"].nunique()) if kind == "piecewise" else None)
    output = dict(model=kind + "_" + calendar, kind=kind, calendar_adjustment=calendar,
                  baseline_strata=("creation quarter" if kind == "primary" else "creation quarter x prior role/experience")
                  if calendar == "quarter_baseline" else ("none" if kind == "primary" else "prior role/experience"),
                  counts=counts, covariates=columns)
    try:
        X = selected[columns]
        if not len(selected) or not np.isfinite(X.to_numpy()).all():
            raise ValueError("Empty or nonfinite planned feature matrix")
        if np.linalg.matrix_rank(X.to_numpy()) != len(columns):
            raise ValueError("Rank-deficient planned feature matrix")
        centered = X - X.groupby(selected["_stratum"]).transform("mean")
        if np.linalg.matrix_rank(centered.to_numpy()) != len(columns):
            raise ValueError("Covariates are not identified after baseline-stratum centering")
        clusters = counts["informative_quarter_clusters"]
        if clusters < 2:
            raise ValueError("Fewer than two informative quarter clusters")
        model = PHReg(selected["_exit"], X, status=selected["_event"],
                      entry=selected["_entry"] if kind == "piecewise" else None,
                      strata=selected["_stratum"], ties="efron")
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            result = model.fit(groups=selected["creation_quarter"].to_numpy())
        output["warnings"] = [dict(category=type(w.message).__name__, message=str(w.message)) for w in captured]
        if any(isinstance(w.message, (ConvergenceWarning, HessianInversionWarning)) for w in captured):
            raise ValueError("Optimizer or information-matrix warning; estimate retained as failed")
        covariance = np.asarray(result.cov_params())
        information = -np.asarray(model.hessian(result.params))
        eigenvalues = np.linalg.eigvalsh(information)
        score_norm = float(np.linalg.norm(model.score(result.params), ord=np.inf))
        if not (np.isfinite(result.params).all() and np.isfinite(covariance).all()):
            raise ValueError("Nonfinite coefficients or clustered covariance")
        if (eigenvalues.min() <= 0 or np.linalg.matrix_rank(information) != len(columns)
                or score_norm > 1e-5):
            raise ValueError("Non-positive information or non-negligible optimized score")
        critical = stats.t.ppf(.975, clusters - 1)
        effects = []
        for label in (["constant"] if kind == "primary" else ["through_day_30", "after_day_30"]):
            contrast = np.zeros(len(columns)); contrast[0] = 1
            if label == "after_day_30":
                contrast[1] = 1
            coefficient = float(contrast @ result.params)
            variance = float(contrast @ covariance @ contrast)
            if variance < 0:
                raise ValueError("Negative clustered contrast variance")
            se = float(np.sqrt(variance))
            effects.append(dict(period=label, log_hazard_coefficient=coefficient,
                                cluster_standard_error=se, hazard_ratio=float(np.exp(coefficient)),
                                lower_95=float(np.exp(coefficient - critical * se)),
                                upper_95=float(np.exp(coefficient + critical * se))))
        output.update(status="identified_fit", concentration_effects=effects,
                      coefficient_vector=dict(zip(columns, map(float, result.params))),
                      log_likelihood=float(result.llf), score_infinity_norm=score_norm,
                      information_minimum_eigenvalue=float(eigenvalues.min()),
                      information_condition_number=float(eigenvalues.max() / eigenvalues.min()),
                      interval_method="Quarter-clustered sandwich covariance; Student t with informative quarters minus one df")
    except (ValueError, np.linalg.LinAlgError, FloatingPointError) as error:
        output.update(status="failed_fit", error_type=type(error).__name__, error=str(error))
    return output


def assert_baseline_reproduction(fits, primary_reference, piecewise_reference):
    """The diagnostic may change calendar adjustment, never the frozen baseline."""
    for key, references in [("primary_linear", [primary_reference["primary"]["coefficients"][0]]),
                            ("piecewise_linear", piecewise_reference["concentration_effects"])]:
        fit = fits[key]
        if fit["status"] != "identified_fit":
            raise ValueError("Frozen baseline reproduction failed: " + key)
        for calculated, expected in zip(fit["concentration_effects"], references):
            for field in ["log_hazard_coefficient", "cluster_standard_error", "hazard_ratio", "lower_95", "upper_95"]:
                if not np.isclose(calculated[field], expected[field], atol=1e-10, rtol=1e-9):
                    raise ValueError("Frozen baseline differs: " + key + "/" + field)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "Paper/results/pr-census")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    plan_path = DEFAULT_OUTPUT / "audit_plan.json"
    plan = json.loads(plan_path.read_text())
    if plan["calendar_spline"]["degrees_of_freedom"] != 4 or len(plan["models"]) != 6:
        raise ValueError("Unsupported audit plan")
    for name in ["coverage.json", "summary.json"]:
        metadata = json.loads((args.input / name).read_text())
        if not metadata.get("metadata_complete"):
            raise ValueError("Complete metadata census required")
        if name == "coverage.json" and not metadata.get("lifecycle_validated"):
            raise ValueError("Validated lifecycle histories required")
    source = args.input / "human_pr_metadata_features.csv"
    original = pd.read_csv(source)
    required = ["lag90_merger_hhi", "lag90_merges", "backlog_at_submission", "prior_author_prs",
                "prior_integrator_author", "calendar_years", "duration_days", "merge_event", "creation_quarter"]
    frame = original.dropna(subset=required).copy().reset_index(drop=True)
    spline, spline_info = calendar_basis(frame)
    decomposition = variance_decomposition(frame)
    y = frame["lag90_merger_hhi"].to_numpy()
    for label, basis in [("linear_calendar", frame[["calendar_years"]].to_numpy()), ("spline4_calendar", spline)]:
        X = np.column_stack([np.ones(len(frame)), basis])
        residual = y - X @ np.linalg.lstsq(X, y, rcond=None)[0]
        decomposition[label + "_exposure_r_squared"] = float(1 - np.sum(residual ** 2) / decomposition["total_sum_squares"])
    rows = []
    for quarter, group in frame.groupby("creation_quarter"):
        values = group["lag90_merger_hhi"]
        rows.append(dict(creation_quarter=quarter, eligible_prs=len(group),
                         merge_events=int(group["merge_event"].sum()), hhi_mean=float(values.mean()),
                         hhi_sd=float(values.std(ddof=1)) if len(values) > 1 else 0.,
                         hhi_min=float(values.min()), hhi_max=float(values.max()),
                         hhi_range=float(values.max() - values.min()),
                         within_sum_squares=float(np.sum((values - values.mean()) ** 2)),
                         late_subjects=int((group["duration_days"] > 30).sum()),
                         late_merge_events=int(((group["duration_days"] > 30) & (group["merge_event"] == 1)).sum())))
    quarters = pd.DataFrame(rows)
    fits = {kind + "_" + calendar: fit_diagnostic(frame, kind, calendar)
            for kind in ["primary", "piecewise"] for calendar in ["linear", "spline4", "quarter_baseline"]}
    references = [ROOT / "Paper/results/inference/cause_specific_merge_models.json",
                  ROOT / "Paper/results/inference/piecewise_merge_model.json"]
    assert_baseline_reproduction(fits, *(json.loads(p.read_text()) for p in references))
    effects = [dict(model=key, **effect) for key, fit in fits.items()
               for effect in fit.get("concentration_effects", [])]
    summary = dict(created_at_utc=datetime.now(timezone.utc).isoformat(), status="Post-pilot exploratory diagnostic",
                   input_prs=len(original), eligible_prs=len(frame), excluded_missing_covariates=len(original) - len(frame),
                   creation_quarters=int(frame["creation_quarter"].nunique()),
                   hhi_minimum=float(y.min()), hhi_maximum=float(y.max()),
                   quarters_with_zero_hhi_range=int((quarters["hhi_range"] <= 1e-12).sum()),
                   quarters_with_hhi_range_at_least_point_one=int((quarters["hhi_range"] >= .1).sum()),
                   exposure_variation=decomposition, calendar_spline=spline_info,
                   planned_fits=len(fits), identified_fits=sum(f["status"] == "identified_fit" for f in fits.values()),
                   failed_fits=[k for k, f in fits.items() if f["status"] != "identified_fit"],
                   frozen_baselines_reproduced=True, limits=plan["limits"],
                   provenance=dict(input_sha256=digest(source), plan_sha256=digest(plan_path),
                                   preview_sha256=digest(DEFAULT_OUTPUT / "preview_fits.json"),
                                   helper_sha256={name: digest(ROOT / "Paper/scripts" / name) for name in HELPERS},
                                   reference_sha256={p.name: digest(p) for p in references},
                                   census_metadata_sha256={name: digest(args.input / name) for name in ["coverage.json", "summary.json"]},
                                   uv_lock_sha256=digest(ROOT / "Paper/uv.lock")))
    args.output.mkdir(parents=True, exist_ok=True)
    quarters.to_csv(args.output / "quarter_exposure.csv", index=False)
    pd.DataFrame(effects).to_csv(args.output / "model_effects.csv", index=False)
    (args.output / "model_fits.json").write_text(json.dumps(fits, indent=2, allow_nan=False) + "\n")
    summary["provenance"]["output_sha256"] = {
        name: digest(args.output / name)
        for name in ["quarter_exposure.csv", "model_effects.csv", "model_fits.json"]
    }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    print(json.dumps(dict(eligible_prs=len(frame), exposure_variation=decomposition,
                         identified_fits=summary["identified_fits"], failed_fits=summary["failed_fits"],
                         concentration_effects=effects), indent=2))


if __name__ == "__main__":
    main()
