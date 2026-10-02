# The image a derivation's query runs in. It holds the two libraries and nothing
# else: the runner and the staging scripts are bind-mounted when a job starts, so
# changing them never needs a rebuild, and nothing is installed at run time,
# which is what lets the query container have no network at all.
#
# Build it in the distro that holds Docker (RUNBOOK, "Running a query to make a
# new dataset"):
#
#   docker build -t munitas-derive-runner:1 worker/derive
#
# duckdb and boto3 are pinned to the same versions as the API's requirements.
FROM python:3.11-slim
RUN pip install --no-cache-dir duckdb==1.5.6 boto3==1.35.24
