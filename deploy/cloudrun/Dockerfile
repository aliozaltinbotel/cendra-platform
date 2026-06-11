# Cloud Run wrapper: fork api image + the DeNA entrypoint (health server
# for worker/beat probes, mode dispatch, no gunicorn --preload — gevent
# monkey-patching breaks ssl under preload).
# Source: DeNA/dify-google-cloud-terraform docker/api (MIT), adapted.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

COPY --chmod=755 entrypoint.sh /entrypoint.sh

ENTRYPOINT ["/bin/bash", "/entrypoint.sh"]
