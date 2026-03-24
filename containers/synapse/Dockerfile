FROM python:3.11-slim

WORKDIR /data

RUN apt-get update && apt-get install -y --no-install-recommends curl fuse \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir matrix-synapse

RUN python -m synapse.generate_signing_key version --directory /data

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8008 8448

ENTRYPOINT ["/entrypoint.sh"]
