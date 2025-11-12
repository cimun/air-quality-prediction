import modal

# ----- Config -----
REPO_URL = "https://github.com/cimun/mlfs-book"
NB_FEATURE = "notebooks/airquality/2_air_quality_feature_pipeline.ipynb"
NB_INFER  = "notebooks/airquality/4_air_quality_batch_inference.ipynb"

# If you have per-run parameters (e.g., sensor/city), add here:
FEATURE_PARAMS = {}  # e.g., {"city": "stockholm", "sensor_id": "xxx"}
INFER_PARAMS   = {}  # e.g., {"forecast_horizon_days": 7}

# Name of your Modal Secret containing HOPSWORKS_API_KEY (and optional others)
HOPSWORKS_SECRET = "hopsworks"

# ----- Modal setup -----
stub = modal.App("air_quality_pipelines")

# Build an image with the tools we need.
image = (
    modal.Image.debian_slim()
    .apt_install("git")
    .env({"PYTHONPATH": "mlfs"})
    .pip_install([
        # minimal tooling for notebook execution; repo requirements will be
        # installed at runtime from the project's requirements.txt so the
        # scheduled job uses the exact project dependencies.
        "papermill", "ipykernel",
    ])
)

def _clone_repo(tempdir: str):
    import subprocess
    subprocess.run(["git", "clone", "--depth", "1", REPO_URL, tempdir], check=True)

def _exec_notebook(nb_path: str, out_path: str, parameters: dict):
    import papermill as pm
    pm.execute_notebook(
        nb_path,
        out_path,
        parameters=parameters or {},
        kernel_name="python3",
        progress_bar=False,
        log_output=True,
    )

@stub.function(
    image=image, 
    schedule=modal.Period(days=1),     # runs daily
    secrets=[modal.Secret.from_name(HOPSWORKS_SECRET)],
    timeout=60 * 20,                    # bump if needed
)
def run_daily_features():
    import os, tempfile, pathlib
    import sys, subprocess
    workdir = tempfile.mkdtemp()
    _clone_repo(workdir)
    # Ensure Python can import the `mlfs` package from the cloned repo.
    # Setting PYTHONPATH here (to the repo root) guarantees the kernel
    # used by papermill will find the `mlfs` package regardless of the
    # working directory inside the Modal execution environment.
    os.environ["PYTHONPATH"] = os.environ.get("PYTHONPATH", "") + (":" if os.environ.get("PYTHONPATH") else "") + workdir
    # Install project-specific requirements from the cloned repository so
    # the notebook runs with the exact dependencies declared by the project.
    req_file = os.path.join(workdir, "requirements.txt")
    if os.path.exists(req_file):
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_file], check=True)
    nb_in  = os.path.join(workdir, NB_FEATURE)
    nb_out = os.path.join(workdir, "out_feature.ipynb")

    # optional: pass parameters like sensor/city here
    _exec_notebook(nb_in, nb_out, FEATURE_PARAMS)

    # if the notebook writes artifacts (PNGs/CSV) into the repo,
    # you can push them somewhere here (e.g., S3, GH Pages). Otherwise, done.

@stub.function(
    image=image,
    # optional: run a little later than features (e.g., 25h cadence or a cron)
    schedule=modal.Period(days=1),
    secrets=[modal.Secret.from_name(HOPSWORKS_SECRET)],
    timeout=60 * 20,
)
def run_daily_inference():
    import os, tempfile
    import sys, subprocess
    workdir = tempfile.mkdtemp()
    _clone_repo(workdir)
    # Make sure notebooks executed here can `import mlfs` from the
    # freshly cloned repository.
    os.environ["PYTHONPATH"] = os.environ.get("PYTHONPATH", "") + (":" if os.environ.get("PYTHONPATH") else "") + workdir
    # Install project requirements if present
    req_file = os.path.join(workdir, "requirements.txt")
    if os.path.exists(req_file):
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_file], check=True)
    nb_in  = os.path.join(workdir, NB_INFER)
    nb_out = os.path.join(workdir, "out_infer.ipynb")

    _exec_notebook(nb_in, nb_out, INFER_PARAMS)

    # If your inference notebook outputs a PNG dashboard,
    # optionally commit/publish it from here (see note below).

if __name__ == "__main__":
    # Deploy both scheduled functions
    stub.deploy()
    # Run once interactively if you want to test now:
    #with stub.run():
    #    run_daily_features.remote()
    #   run_daily_inference.remote()
