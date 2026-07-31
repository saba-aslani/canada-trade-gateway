"""Train a commercial border wait-time model on the CBSA archive.

Design notes, because the modelling choices here are the point:

1. Two stages, not one. Roughly three quarters of commercial readings are
   'No delay', so a single regressor spends its capacity predicting zero and
   reports a flattering error on a target that is mostly zero. Instead:
       stage 1  P(delay > 0)                  — classifier
       stage 2  E[delay | delay > 0]          — regressor on log1p(minutes)
       combined expected delay = stage1 * stage2
   This is a hurdle model, and it keeps the two operational questions separate:
   "will I be held up" and "for how long if I am".

2. Temporal split, not random. Rows from the same hour are near-duplicates, so
   a random split leaks and produces a meaningless score. Train is 2016-2018,
   test is 2019 — the model is only ever asked about a period it has not seen.

3. A lookup-table baseline is scored alongside. The features are all
   categorical (crossing, hour, weekday, month), so a per-cell historical mean
   is a genuinely strong competitor. If the gradient-boosted model cannot beat
   it, that is the finding worth reporting, not something to hide.

Outputs are written back to the warehouse so the dashboard reads the same
numbers this script measured:
    analytics_marts.ml_model_metrics       one row per metric per evaluation
    analytics_marts.ml_wait_predictions    expected delay by crossing/day/hour

Usage:
    python train_wait_model.py
    python train_wait_model.py --no-write     # evaluate without touching the DB
"""
from __future__ import annotations

import argparse
import logging
import sys

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    mean_absolute_error,
    roc_auc_score,
)
from xgboost import XGBClassifier, XGBRegressor

from db import get_connection

STAGING = "analytics_staging"
MARTS = "analytics_marts"

TRAIN_MAX_YEAR = 2018      # 2016-2018 train, 2019 test
TEST_YEAR = 2019
RANDOM_STATE = 42

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("wait_model")


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

def load_history(conn) -> pd.DataFrame:
    """Commercial readings that carry a real measurement."""
    sql = f"""
        select crossing_name,
               delay_minutes,
               local_hour_pt  as hour_of_day,
               local_dow_pt   as day_of_week,
               local_month_pt as month_of_year,
               local_year_pt  as year
        from {STAGING}.stg_border_waits_historical
        where traffic_type = 'commercial'
          and delay_status = 'reported'
          and delay_minutes is not null
    """
    df = pd.read_sql(sql, conn)
    for column in ("hour_of_day", "day_of_week", "month_of_year", "year"):
        df[column] = df[column].astype(int)
    df["delay_minutes"] = df["delay_minutes"].astype(float)
    return df


def load_live(conn) -> pd.DataFrame:
    """Readings collected by this project, used to measure drift."""
    sql = f"""
        select crossing_name,
               delay_minutes,
               local_hour_pt  as hour_of_day,
               local_dow_pt   as day_of_week,
               local_month_pt as month_of_year
        from {MARTS}.fct_border_waits
        where traffic_type = 'commercial'
          and direction = 'canada'
          and delay_status = 'reported'
          and delay_minutes is not null
    """
    df = pd.read_sql(sql, conn)
    if df.empty:
        return df
    for column in ("hour_of_day", "day_of_week", "month_of_year"):
        df[column] = df[column].astype(int)
    df["delay_minutes"] = df["delay_minutes"].astype(float)
    return df


def build_features(df: pd.DataFrame, crossing_codes: dict[str, int]) -> pd.DataFrame:
    """Categorical identifiers plus cyclical encodings of time.

    Hour 23 and hour 0 are adjacent; an ordinal encoding tells the model they
    are 23 apart. Sine and cosine pairs restore that adjacency, which matters
    because the overnight lull spans midnight.
    """
    out = pd.DataFrame(index=df.index)
    out["crossing_code"] = df["crossing_name"].map(crossing_codes).fillna(-1).astype(int)
    out["hour_of_day"] = df["hour_of_day"]
    out["day_of_week"] = df["day_of_week"]
    out["month_of_year"] = df["month_of_year"]
    out["is_weekend"] = df["day_of_week"].isin([0, 6]).astype(int)
    out["hour_sin"] = np.sin(2 * np.pi * df["hour_of_day"] / 24)
    out["hour_cos"] = np.cos(2 * np.pi * df["hour_of_day"] / 24)
    out["month_sin"] = np.sin(2 * np.pi * df["month_of_year"] / 12)
    out["month_cos"] = np.cos(2 * np.pi * df["month_of_year"] / 12)
    return out


# --------------------------------------------------------------------------
# Baseline
# --------------------------------------------------------------------------

def lookup_baseline(train: pd.DataFrame, target: pd.DataFrame) -> np.ndarray:
    """Historical mean delay for the matching crossing/hour/weekday/month cell.

    Falls back to progressively coarser cells when a combination was never
    observed in training, ending at the global mean. Without the fallback the
    baseline would quietly drop rows and look better than it is.
    """
    keys = [
        ["crossing_name", "hour_of_day", "day_of_week", "month_of_year"],
        ["crossing_name", "hour_of_day", "day_of_week"],
        ["crossing_name", "hour_of_day"],
        ["crossing_name"],
    ]
    prediction = pd.Series(np.nan, index=target.index)
    for key in keys:
        table = train.groupby(key)["delay_minutes"].mean()
        joined = target.join(table.rename("value"), on=key)["value"]
        prediction = prediction.fillna(joined)
    return prediction.fillna(train["delay_minutes"].mean()).to_numpy()


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------

def fit_hurdle(x_train: pd.DataFrame, y_train: np.ndarray):
    """Stage 1 classifier and stage 2 regressor."""
    is_delayed = (y_train > 0).astype(int)

    classifier = XGBClassifier(
        n_estimators=400,
        max_depth=6,
        learning_rate=0.08,
        subsample=0.9,
        colsample_bytree=0.9,
        eval_metric="logloss",
        random_state=RANDOM_STATE,
        n_jobs=4,
    )
    classifier.fit(x_train, is_delayed)

    delayed = y_train > 0
    regressor = XGBRegressor(
        n_estimators=400,
        max_depth=6,
        learning_rate=0.08,
        subsample=0.9,
        colsample_bytree=0.9,
        random_state=RANDOM_STATE,
        n_jobs=4,
    )
    # log1p compresses a long right tail so a handful of two-hour backups do
    # not dominate the loss; predictions are inverted with expm1.
    regressor.fit(x_train[delayed], np.log1p(y_train[delayed]))

    return classifier, regressor


def predict_expected(classifier, regressor, x: pd.DataFrame) -> np.ndarray:
    probability = classifier.predict_proba(x)[:, 1]
    magnitude = np.expm1(regressor.predict(x)).clip(min=0)
    return probability * magnitude


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

DDL = f"""
create table if not exists {MARTS}.ml_model_metrics (
    evaluated_at  timestamptz not null default now(),
    evaluation    text not null,
    metric        text not null,
    value         double precision,
    n_rows        bigint
);

create table if not exists {MARTS}.ml_wait_predictions (
    crossing_name           text not null,
    day_of_week             integer not null,
    hour_of_day             integer not null,
    delay_probability       double precision,
    expected_delay_minutes  double precision,
    generated_at            timestamptz not null default now(),
    primary key (crossing_name, day_of_week, hour_of_day)
);
"""


def write_metrics(conn, rows: list[tuple]) -> None:
    with conn.cursor() as cur:
        cur.execute(DDL)
        cur.execute(f"delete from {MARTS}.ml_model_metrics")
        cur.executemany(
            f"insert into {MARTS}.ml_model_metrics (evaluation, metric, value, n_rows) "
            "values (%s, %s, %s, %s)",
            rows,
        )
    conn.commit()


def write_predictions(conn, predictions: pd.DataFrame) -> None:
    with conn.cursor() as cur:
        cur.execute(DDL)
        cur.execute(f"delete from {MARTS}.ml_wait_predictions")
        cur.executemany(
            f"insert into {MARTS}.ml_wait_predictions "
            "(crossing_name, day_of_week, hour_of_day, delay_probability, "
            " expected_delay_minutes) values (%s, %s, %s, %s, %s)",
            list(predictions.itertuples(index=False, name=None)),
        )
    conn.commit()


# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    conn = get_connection()
    try:
        log.info("Loading historical commercial readings ...")
        history = load_history(conn)
        log.info("  %d rows, %d crossings, years %d-%d",
                 len(history), history["crossing_name"].nunique(),
                 history["year"].min(), history["year"].max())

        train = history[history["year"] <= TRAIN_MAX_YEAR]
        test = history[history["year"] == TEST_YEAR]
        if train.empty or test.empty:
            log.error("Temporal split produced an empty side — check the year range.")
            return 1
        log.info("  train %d rows (<=%d), test %d rows (%d)",
                 len(train), TRAIN_MAX_YEAR, len(test), TEST_YEAR)

        crossing_codes = {
            name: index
            for index, name in enumerate(sorted(train["crossing_name"].unique()))
        }

        x_train = build_features(train, crossing_codes)
        x_test = build_features(test, crossing_codes)
        y_train = train["delay_minutes"].to_numpy()
        y_test = test["delay_minutes"].to_numpy()

        log.info("Fitting hurdle model ...")
        classifier, regressor = fit_hurdle(x_train, y_train)

        probability = classifier.predict_proba(x_test)[:, 1]
        expected = predict_expected(classifier, regressor, x_test)
        baseline = lookup_baseline(train, test)

        metrics = [
            ("holdout_2019", "stage1_roc_auc",
             float(roc_auc_score((y_test > 0).astype(int), probability)), len(test)),
            ("holdout_2019", "stage1_pr_auc",
             float(average_precision_score((y_test > 0).astype(int), probability)), len(test)),
            ("holdout_2019", "stage1_brier",
             float(brier_score_loss((y_test > 0).astype(int), probability)), len(test)),
            ("holdout_2019", "model_mae_minutes",
             float(mean_absolute_error(y_test, expected)), len(test)),
            ("holdout_2019", "baseline_mae_minutes",
             float(mean_absolute_error(y_test, baseline)), len(test)),
            ("holdout_2019", "delayed_share",
             float((y_test > 0).mean()), len(test)),
        ]

        model_mae = metrics[3][2]
        baseline_mae = metrics[4][2]
        improvement = (baseline_mae - model_mae) / baseline_mae * 100
        metrics.append(
            ("holdout_2019", "improvement_over_baseline_pct", float(improvement), len(test))
        )

        log.info("Holdout 2019 — ROC-AUC %.3f | PR-AUC %.3f | Brier %.4f",
                 metrics[0][2], metrics[1][2], metrics[2][2])
        log.info("Holdout 2019 — model MAE %.2f min vs lookup baseline %.2f min (%.1f%%)",
                 model_mae, baseline_mae, improvement)

        # Drift: does a pre-pandemic model still describe traffic today?
        live = load_live(conn)
        if len(live) >= 50:
            x_live = build_features(live, crossing_codes)
            y_live = live["delay_minutes"].to_numpy()
            live_expected = predict_expected(classifier, regressor, x_live)
            live_baseline = lookup_baseline(train, live)
            metrics += [
                ("live_2026", "model_mae_minutes",
                 float(mean_absolute_error(y_live, live_expected)), len(live)),
                ("live_2026", "baseline_mae_minutes",
                 float(mean_absolute_error(y_live, live_baseline)), len(live)),
                ("live_2026", "delayed_share",
                 float((y_live > 0).mean()), len(live)),
            ]
            log.info("Live 2026 — model MAE %.2f min over %d readings "
                     "(delayed share %.1f%% vs %.1f%% in 2019)",
                     metrics[-3][2], len(live),
                     metrics[-1][2] * 100, float((y_test > 0).mean()) * 100)
        else:
            log.warning("Only %d live readings — too few to measure drift yet.", len(live))

        # Prediction grid for the dashboard.
        grid = pd.MultiIndex.from_product(
            [sorted(crossing_codes), range(7), range(24)],
            names=["crossing_name", "day_of_week", "hour_of_day"],
        ).to_frame(index=False)
        grid["month_of_year"] = 7  # representative summer month
        x_grid = build_features(grid, crossing_codes)
        grid["delay_probability"] = classifier.predict_proba(x_grid)[:, 1]
        grid["expected_delay_minutes"] = predict_expected(classifier, regressor, x_grid)
        grid = grid[[
            "crossing_name", "day_of_week", "hour_of_day",
            "delay_probability", "expected_delay_minutes",
        ]]

        if args.no_write:
            log.info("--no-write set; skipping database writes.")
        else:
            write_metrics(conn, metrics)
            write_predictions(conn, grid)
            log.info("Wrote %d metrics and %d predictions to the warehouse.",
                     len(metrics), len(grid))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
