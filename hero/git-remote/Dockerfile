# A fully-offline bare git remote holding the acme/analytics dbt project.
#
# The worker can read the failing source at a ref from here, and V4's PR opener
# has a real branch/push target — no GitHub, no network egress. Served over the
# git protocol (git://git-remote:9418/analytics.git).
FROM alpine:3.20

# `git-daemon` is a separate alpine package from `git` (it ships `git daemon`).
RUN apk add --no-cache git git-daemon

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 9418
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
