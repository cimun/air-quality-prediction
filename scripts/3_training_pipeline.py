import sys
import os
import json
import warnings
from pathlib import Path
from datetime import datetime, date
import re

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
import numpy as np
import matplotlib.pyplot as plt
from xgboost import XGBRegressor, plot_importance
from sklearn.metrics import mean_squared_error, r2_score
import hopsworks
from mlfs.airquality import util

# ---------- Hopsworks login ----------
if settings.HOPSWORKS_API_KEY is not None:
    os.environ["HOPSWORKS_API_KEY"] = settings.HOPSWORKS_API_KEY.get_secret_value()
project = hopsworks.login(engine="python")
fs = project.get_feature_store()
mr = project.get_model_registry()

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

def train_one_sensor(city: str, street: str) -> None:
    street_slug = slugify(street)
    aq_fg = fs.get_feature_group(name=f"air_quality_{street_slug}", version=1)
    wx_fg = fs.get_feature_group(name=f"weather_{street_slug}", version=1)

    selected = aq_fg.select(["pm25", "date"]).join(wx_fg.select_features(), on=["city"])
    fv = fs.get_or_create_feature_view(
        name=f"air_quality_fv_{street_slug}",
        description=f"Features for {city}/{street}",
        version=1,
        labels=["pm25"],
        query=selected,
    )

    # ---- start split for test data
    start_date_test_data = "2025-05-01"
    # Convert string to datetime object
    test_start = datetime.strptime(start_date_test_data, "%Y-%m-%d")
    
    X_train, X_test, y_train, y_test = fv.train_test_split(test_start=test_start)
    X_features = X_train.drop(columns=["date"])
    X_test_features = X_test.drop(columns=["date"])

    model = XGBRegressor()
    model.fit(X_features, y_train)

    y_pred = model.predict(X_test_features)
    mse = mean_squared_error(y_test.iloc[:, 0], y_pred)
    r2 = r2_score(y_test.iloc[:, 0], y_pred)
    print(f"[{city} / {street}] MSE={mse:.4f} R2={r2:.4f}")

    df = y_test.copy()
    df["predicted_pm25"] = y_pred
    df["date"] = X_test["date"]
    df = df.sort_values(by=["date"])

    out_dir = Path(f"air_quality_model_{street_slug}")
    img_dir = out_dir / "images"
    out_dir.mkdir(exist_ok=True)
    img_dir.mkdir(parents=True, exist_ok=True)

    hindcast_path = img_dir / "pm25_hindcast.png"
    plt = util.plot_air_quality_forecast(city, street, df, str(hindcast_path), hindcast=True)
    #plt.show()

    plot_importance(model)
    plt.savefig(img_dir / "feature_importance.png")
    #plt.show()

    model.save_model(str(out_dir / "model.json"))
    metrics = {"MSE": str(mse), "R squared": str(r2)}

    reg_name = f"air_quality_xgboost_model_{street_slug}"
    py_model = mr.python.create_model(
        name=reg_name,
        metrics=metrics,
        feature_view=fv,
        description=f"PM2.5 predictor for {city}/{street}",
    )
    py_model.save(str(out_dir))
    print(f"[{city} / {street}] Model saved to registry as {reg_name}")

# ---------- Main ----------
def main():
    sensors_csv = os.environ.get("SENSORS_CSV", str(root_dir / "data" / "sensors.csv"))
    if not Path(sensors_csv).exists():
        raise FileNotFoundError(f"Missing sensors CSV: {sensors_csv}")

    sensors = get_sensor_rows(sensors_csv)
    print(f"Training models for {len(sensors)} sensors from {sensors_csv}")

    for _, row in sensors.iterrows():
        city = row["city"].strip()
        street = row["street"].strip()
        try:
            print(f"\n=== Training: {city} / {street} ===")
            train_one_sensor(city, street)
        except Exception as e:
            print(f"! Error training {city} / {street}: {e}")

    print("\nAll sensors processed.")

if __name__ == "__main__":
    main()
