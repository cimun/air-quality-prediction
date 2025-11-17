#!/usr/bin/env python3
import sys
import os
import io
import json
import time
import warnings
import re
from pathlib import Path
from datetime import datetime, timedelta

from numpy import double

warnings.filterwarnings("ignore", module="IPython")

# ---------- Paths / PYTHONPATH ----------
root_dir = Path().absolute()
if root_dir.parts[-1:] == ("airquality",):
    root_dir = Path(*root_dir.parts[:-1])
if root_dir.parts[-1:] == ("notebooks",):
    root_dir = Path(*root_dir.parts[:-1])
root_dir = root_dir.resolve()
if str(root_dir) not in sys.path:
    sys.path.append(str(root_dir))
print(f"Local environment — project root: {root_dir}")

if root_dir not in sys.path:
    sys.path.append(root_dir)
    print(f"Added to PYTHONPATH: {root_dir}")

# ---------- Settings ----------
from mlfs import config
settings = config.HopsworksSettings(_env_file=str(root_dir / ".env"))

# ---------- Imports ----------
import pandas as pd
import matplotlib.pyplot as plt
from xgboost import XGBRegressor
import hopsworks
#from huggingface_hub import HfApi, CommitOperationAdd
from mlfs.airquality import util

# ---------- Hopsworks login ----------
if settings.HOPSWORKS_API_KEY is not None:
    os.environ["HOPSWORKS_API_KEY"] = settings.HOPSWORKS_API_KEY.get_secret_value()

project = hopsworks.login(engine="python")
fs = project.get_feature_store()
mr = project.get_model_registry()
secrets = hopsworks.get_secrets_api()

# ---------- Helpers ----------
def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s-]+", "_", text)
    return text

def get_sensor_rows(sensors_csv: str) -> pd.DataFrame:
    req = {"AQICN_URL", "country", "city", "street", "latitude", "longitude"}
    df = pd.read_csv(sensors_csv, dtype=str).fillna("")
    missing = req - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns in sensors CSV: {sorted(missing)}")
    return df

def run_inference_for_sensor(city: str, street: str) -> None:
    """Run batch inference for one sensor, store results and upload PNGs."""
    street_slug = slugify(street)
    today = datetime.now()
    tomorrow = today + timedelta(days=1)
    print(f"\n=== Inference for {city} / {street} ({today.date()}) ===")

    # ----- Load model -----
    model_name = f"air_quality_xgboost_model_{street_slug}"
    try:
        model = mr.get_model(name=model_name, version=1)
    except Exception:
        print(f"! Model not found for {city}/{street}: {model_name}")
        return

    fv = model.get_feature_view()
    saved_model_dir = model.download()
    xgb_model = XGBRegressor()
    xgb_model.load_model(saved_model_dir + "/model.json")

    # ----- Load weather data -----
    weather_fg = fs.get_feature_group(name=f"weather_{street_slug}", version=1)
    aq_fg = fs.get_feature_group(name=f"air_quality_{street_slug}", version=1)
    aq_df = aq_fg.read()
    batch_df = weather_fg.filter(weather_fg.date >= today).read()
    batch_df = batch_df.sort_values("date")

    lag_1 = aq_df.loc[aq_df["date"].dt.date == today.date(), "pm25"].iloc[0]
    lag_2 = aq_df.loc[aq_df["date"].dt.date == (today - timedelta(days=1)).date(), "pm25"].iloc[0]
    lag_3 = aq_df.loc[aq_df["date"].dt.date == (today - timedelta(days=2)).date(), "pm25"].iloc[0]

    batch_df["predicted_pm25"] = 0.0
    batch_df["predicted_pm25"] = batch_df["predicted_pm25"].astype(double)
    batch_df["pm25_lag_1"] = float(0.0)
    batch_df["pm25_lag_2"] = float(0.0)
    batch_df["pm25_lag_3"] = float(0.0)

    for idx, row in batch_df.iterrows():
        print(f"Predicting for {row['date'].date()} with lags: {lag_1}, {lag_2}, {lag_3}")
        batch_df.at[idx, "pm25_lag_1"] = lag_1
        batch_df.at[idx, "pm25_lag_2"] = lag_2
        batch_df.at[idx, "pm25_lag_3"] = lag_3
        features = [
            lag_1,
            lag_2,
            lag_3,
            float(row.get("temperature_2m_mean")),
            float(row.get("precipitation_sum")),
            float(row.get("wind_speed_10m_max")),
            float(row.get("wind_direction_10m_dominant")),
            float(row.get("surface_pressure_mean")),
            float(row.get("relative_humidity_2m_mean")),
            float(row.get("cloud_cover_mean")),
        ]
        pred = xgb_model.predict([features])[0]
        # Set lag columns for the current row (the values used for this prediction)
        batch_df.at[idx, "predicted_pm25"] = pred
        # Shift lags for the next prediction
        lag_2, lag_3 = lag_1, lag_2
        lag_1 = pred

    batch_df["street"] = street
    batch_df["city"] = city
    batch_df["country"] = batch_df.get("country", city)
    batch_df["days_before_forecast_day"] = range(1, len(batch_df) + 1)
    batch_df = batch_df.sort_values(by=["date"])

    # ----- Save prediction chart -----
    docs_dir = root_dir / "docs" / "air-quality" / "assets" / "img"
    docs_dir.mkdir(parents=True, exist_ok=True)
    pred_path = docs_dir / f"pm25_forecast_{street_slug}.png"
    plt = util.plot_air_quality_forecast(city, street, batch_df, str(pred_path))
    plt.close()

    # ----- Monitoring feature group -----
    monitor_fg = fs.get_or_create_feature_group(
        name=f"aq_predictions_{street_slug}",
        description=f"Air Quality prediction monitoring for {street}, {city}",
        version=1,
        primary_key=["city", "street", "date", "days_before_forecast_day"],
        event_time="date",
    )
    monitor_fg.insert(batch_df, wait=True)

    # ----- Hindcast -----
    air_fg = fs.get_feature_group(name=f"air_quality_{street_slug}", version=1)
    air_df = air_fg.read()
    outcome_df = air_df[["date", "pm25"]]
    preds_df = monitor_fg.filter(monitor_fg.days_before_forecast_day == 1).read()[
        ["date", "predicted_pm25"]
    ]

    hindcast_df = pd.merge(preds_df, outcome_df, on="date", how="inner")
    hindcast_df = hindcast_df.sort_values(by=["date"])
    if len(hindcast_df) == 0:
        hindcast_df = util.backfill_predictions_for_monitoring(
            weather_fg, air_df, monitor_fg, xgb_model
        )

    hindcast_path = docs_dir / f"pm25_hindcast_{street_slug}.png"
    plt = util.plot_air_quality_forecast(city, street, hindcast_df, str(hindcast_path), hindcast=True)
    plt.close()

    # ----- Upload to Hopsworks -----
    dataset_api = project.get_dataset_api()
    today_str = today.strftime("%Y-%m-%d")
    hops_path = f"Resources/airquality/{city}_{street}_{today_str}"
    if not dataset_api.exists("Resources/airquality"):
        dataset_api.mkdir("Resources/airquality")
    dataset_api.upload(str(pred_path), hops_path, overwrite=True)
    dataset_api.upload(str(hindcast_path), hops_path, overwrite=True)

    print(f"✓ Uploaded forecast and hindcast PNGs for {city} / {street}")


# ---------- Main ----------
def main():
    sensors_csv = os.environ.get("SENSORS_CSV", str(root_dir / "data" / "sensors.csv"))
    if not Path(sensors_csv).exists():
        raise FileNotFoundError(f"Missing sensors CSV: {sensors_csv}")

    sensors = get_sensor_rows(sensors_csv)
    print(f"Running batch inference for {len(sensors)} sensors")

    for _, row in sensors.iterrows():
        city = row["city"].strip()
        street = row["street"].strip()
        try:
            run_inference_for_sensor(city, street)
        except Exception as e:
            print(f"! Error processing {city} / {street}: {e}")

    print("\nAll sensors processed for batch inference.")

if __name__ == "__main__":
    main()
