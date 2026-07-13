# Hero-pipeline Airflow image (Seam-3 harness, docs/design/HERO-PIPELINE.md).
#
# The lightest thing that runs a BashOperator dbt task on a laptop: a single
# apache/airflow container (standalone). dbt is installed into its OWN venv at
# /opt/dbt-venv so Airflow's tightly-pinned deps and dbt's never conflict; the
# DAG's BashOperator calls /opt/dbt-venv/bin/dbt. dbt pins match the verification
# sandbox image (sandbox/Dockerfile) so the DAG and the sandbox agree.
FROM apache/airflow:2.10.4-python3.12

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    # /opt is root-owned; hand the dbt venv dir to the airflow user so the
    # unprivileged build step below can populate it.
    && mkdir -p /opt/dbt-venv \
    && chown -R airflow: /opt/dbt-venv

USER airflow
# Isolated dbt venv (never mixed into the Airflow environment).
RUN python -m venv /opt/dbt-venv \
    && /opt/dbt-venv/bin/pip install --no-cache-dir \
        "dbt-core==1.9.4" "dbt-postgres==1.9.0"

ENV DBT_VENV=/opt/dbt-venv \
    PATH=/opt/dbt-venv/bin:${PATH}
