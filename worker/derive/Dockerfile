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
#
# setuptools and wheel come with the base image, are build tools nothing here runs, and setuptools carries a copy of a library under the LGPL (autocommand), so
# they are removed. verify/v176_licences.py looks inside this image for it.
FROM python:3.11-slim
RUN pip install --no-cache-dir duckdb==1.5.6 boto3==1.35.24 && pip uninstall -y setuptools wheel
