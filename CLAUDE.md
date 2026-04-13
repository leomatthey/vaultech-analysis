# VaultTech — Forging Line Cycle Time Analysis

## Project Goals

Two equally important goals:
1. **Execution**: complete all 12 tasks correctly and deliver on time (deadline: 14 April 2026)
2. **Exam preparation**: You must explain EVERY decision you take. This project is the primary study resource — everything done in this project must contribute to the users learning process by including the most helpful explainations of the *why* behind each step, not just the code. For low impact decisions, brief explanations suffice.

## The Product & Manufacturing Process
*Further detail: `docs/01_TheProduct.md`, `docs/02_ManufacturingProcess.md`*

The forging line produces steel track chain links for heavy earthmoving equipment. A heated steel billet travels through 4 zones in ~56–59 seconds: **Furnace** (time-zero reference, no signal) → **Main Press** (4 strikes) → **Auxiliary Press** (deburring/coining) → **Quench Bath** (thermal hardening). A new piece exits every 11–16 seconds (OEE cycle time).

## Data Cleaning

8 cleaning steps applied in predefined order: *Further detail: `docs/03_CleaningUpData.md`*

## Data Architecture
*Further detail: `docs/04_DataArchitecture.md`*

Medallion pattern: **Bronze → Silver → Gold**

| Layer | Storage | Content |
|---|---|---|
| Bronze | PostgreSQL `bronze` schema | Raw PLC signal/value pairs (~1.4M rows) |
| Silver | PostgreSQL `silver.clean_pieces` | Cleaned, 1 row/piece (~169k rows) |
| Gold | `data/gold/pieces.parquet` | Enriched with partial times + production runs |

Pipeline: `01_bronze_to_silver.ipynb` → `02_silver_to_gold.ipynb` → `03_build_clean_dataset.ipynb` (quality gate)

## Predictive Model
*Further detail: `docs/05_PredictiveModel.md`*

**Problem**: XGBoost Regressor predicts `lifetime_bath_s` (total travel time) using only data available after the 2nd strike — ~18s into the 58s journey — to enable real-time delay alerts while the piece is still on the line.

## Technical Architecture

**Local**: CSV data → PostgreSQL bronze → silver (notebook 01, incremental) → gold parquet (notebook 02, full regen) → XGBoost model (notebook 05) → `Predictor` class → Streamlit app → Docker container

**AWS** (eu-west-1): model tar.gz → S3 → SageMaker Model Registry → real-time endpoint → Streamlit app calls endpoint via boto3 → Docker image → ECR → ECS/Fargate (public IP, port 8501)

## Repository Structure

```
├── app/                    Streamlit dashboard
├── data/
│   ├── *.csv.gz            Raw PLC data (for seeding PostgreSQL)
│   └── gold/               Generated parquet (gitignored)
├── deploy/                 SageMaker deployment script + README
├── docs/                   Project documentation (01–05) + FINAL_PROJECT.md
├── infra/                  docker-compose.yml + .env for local stack
├── migrations/
│   ├── sql/                Flyway migrations (V001–V008)
│   └── DATABASE.md         Full schema reference
├── models/                 Trained XGBoost model + metadata (generated)
├── notebooks/              Analysis pipeline (00–05)
├── scripts/                seed.py (data loader)
├── src/vaultech_analysis/  Python package (inference, app/lab launchers)
└── tests/                  pytest tests (inference, causes, SageMaker)
```

## Task Overview
*Full detail: `docs/FINAL_PROJECT.md`*

Before completing ANY TASK, read through the necessary documents to ensure you have a perfect understanding of all the required components

| Tasks | Focus |
|---|---|
| 1–2 | Environment setup, data exploration |
| 3–4 | Bronze→Silver cleaning, Silver→Gold enrichment + quality gate | 
| 5 | Per-matrix analysis (reference profiles, slow pieces, drift) |
| 6–7 | XGBoost model, `Predictor` class, tests |
| 8–9 | Streamlit dashboard, Dockerfile + local Docker |
| 10 | SageMaker Model Registry + real-time endpoint |
| 11 | Wire app to SageMaker, ECR + ECS/Fargate deployment |
| 12 | Architecture diagram + 5-min demo video |

## Useful commands

| Command | What it does |
|---|---|
| `uv sync` | Install/update dependencies |
| `uv run lab` | Launch JupyterLab |
| `uv run app` | Launch Streamlit dashboard |
| `uv run pytest` | Run all tests |
| `uv run pytest tests/test_app_e2e.py -v` | E2E browser tests (local app) |
| `VAULTECH_E2E_DOCKER=1 uv run pytest tests/test_app_e2e.py -v` | E2E browser tests (Docker container) |
| `uv run python -m vaultech_analysis.inference --die-matrix 5052 --strike2 18.3 --oee 13.5` | CLI prediction |


## Working Protocol — before starting ANY task

1. **Read the task spec**: open `docs/FINAL_PROJECT.md` and read the exact task description — count the specified steps, note the deliverables
2. **Read referenced docs**: every doc mentioned in the task spec (e.g., `03_CleaningUpData.md`, `05_PredictiveModel.md`) must be read before writing code
3. **Trace the data flow**: identify what produces the input, what this task produces, and what downstream tasks consume the output
4. **Follow the spec exactly**: do not add steps the professor didn't specify, do not remove steps, do not rename or restructure unless the spec says to
5. **Propose before acting**: for non-trivial changes, explain the approach and confirm before writing code — especially if it affects downstream tasks
6. **Fix at the root**: if a change in task N breaks task N+1, the fix belongs where the root cause is — never apply narrow workarounds in downstream tasks that mask an upstream issue

## Critical Rules

1. **Git tag after every task**: `git tag task-NN && git push origin task-NN`