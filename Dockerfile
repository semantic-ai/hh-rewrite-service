FROM semtech/mu-python-template:feature-no-more-meinheld@sha256:a71a2c2dd00760d29729b373687d2aa7f35e74841b9293d93c8bf6c4bc21f6ac

# web.py waits for the triplestore and registers the agent while the worker
# boots; gunicorn's default 30 s timeout would kill a worker still waiting.
ENV GUNICORN_CMD_ARGS="--timeout 120"
