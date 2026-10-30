```dockerfile
# Ships bin/ as a versioned artifact, so the cluster runs the same code the
# repo reviewed.
#
# Three copies of these scripts previously lived in cluster-ops, embedded in
# ConfigMaps, and two of them went stale unnoticed. etcd-health-watcher ran 474
# lines for a day while this repo held 620 — blind to the NOSPACE alarm a
# merged PR said it could see (homelab#934, cluster-ops#38).
#
# A copy drifts silently. An image tag is explicit, reviewable, and bumped by
# ImageUpdater like everything else in this fleet.
#
# Base: python:3.13-alpine plus kubectl, NOT alpine/k8s. The scripts need
# python and kubectl and nothing else; alpine/k8s bundles helm, aws-cli and
# more, at 1563 MB against 117 MB here. Every node pulls this.
#
# amd64 only, deliberately. k8s-pi-node-01 is the only arm64 node and carries
# kubernetes.io/arch=arm64:NoSchedule; the cluster-ops CronJobs set no
# toleration, so they cannot land there. Multi-arch would be build cost for a
# platform this image cannot reach.
FROM python:3.14-alpine

# kubectl tracks the cluster (server v1.36.4): client/server skew must stay within one
# minor, so Renovate bumps patches only and the minor moves with a cluster
# upgrade. v1.34.0 was two minors behind and carried Go 1.24 stdlib CVEs incl.
# a critical; v1.36.6 is built with go1.26.10, the fixed stdlib (homelab#1697).
# renovate: datasource=github-releases depName=kubernetes/kubernetes
ARG KUBECTL_VERSION=v1.36.6

# bash: several scripts are shell, and a few python ones shell out to it.
# ca-certificates: registry and GitHub API calls.
RUN apk add --no-cache bash ca-certificates curl \
    && curl -sSLo /usr/local/bin/kubectl \
         "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && chmod +x /usr/local/bin/kubectl \
    && kubectl version --client=true --output=yaml >/dev/null \
    && apk del curl

# Scripts land under a stable path so CronJobs can reference it directly.
# bin/tests and __pycache__ are excluded via .dockerignore: tests are 29 MB and
# have no business in a runtime image.
COPY bin/ /opt/homelab/bin/

# policy/ too, not just bin/. Eleven scripts under bin/ read a sibling
# policy/*.yaml, and in-cluster those reads resolved to nothing: on
# 2026-09-24 secret-copies-audit reported "declares no credentials" about a
# file that was never in the image. It failed closed, but the packaging gap
# read as a policy problem.
#
# 56K, against prompts/ at 40MB -- which is deliberately NOT shipped, since
# only gen-owui-tool.py reads it and that never runs in-cluster. The contract
# is pinned by bin/tests/test_image_ships_what_scripts_read.py.
COPY policy/ /opt/homelab/policy/

# PyYAML, because shipping policy/ exposed the next layer: nineteen scripts
# under bin/ `import yaml`, and audit-secret-copies.py is the first one a
# CronJob invokes. Its import sits inside load_policy(), which had never been
# reached while the file was absent -- so the missing dependency only
# surfaced once the missing file was fixed.
#
# --only-binary: a musllinux wheel exists for this base (verified on
# python:3.14-alpine, pyyaml 6.0.3), so no compiler is pulled in. If that ever
# stops being true this fails loudly at build time rather than silently
# building from source and bloating an image every node pulls.
#
# Pinned like every other version here. The set of modules the image must
# carry is pinned by bin/tests/test_image_ships_what_scripts_read.py.
RUN pip install --no-cache-dir --only-binary=:all: pyyaml==6.0.3 && \
    # pip is build-time only; its vendored urllib3/msgpack/setuptools carry CVEs (homelab#1697).
    python3 -m pip uninstall -y pip

ENV PATH="/opt/homelab/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1

# Fail the build if the scripts did not actually arrive, rather than shipping
# an image whose entrypoint is missing — the same "assert the work happened"
# rule the scripts inside apply to themselves.
RUN test -x /opt/homelab/bin/etcd-health-watcher.py \
    && python3 -c "import ast,pathlib,sys; \
         [ast.parse(p.read_text()) for p in pathlib.Path('/opt/homelab/bin').glob('*.py')]" \
    && echo "verified: $(ls /opt/homelab/bin/*.py | wc -l) python scripts parse"

USER 65534:65534
```
