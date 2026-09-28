# Grader egress gateway (operator-approved exception, AGENTS.md rule 5).
# Native arm64. Runs only harness code; never runs model-generated code.
FROM alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc
RUN apk add --no-cache dnsmasq=2.90-r3 iptables=1.8.10-r3 ipset=7.21-r2 python3=3.12.13-r0 \
      bind-tools=9.18.49-r0 \
    && adduser -S -D -H -u 102 egressproxy
COPY --chmod=0555 gateway.sh egress_proxy.py probe.py /opt/egress/
ENTRYPOINT ["/bin/sh", "/opt/egress/gateway.sh"]
