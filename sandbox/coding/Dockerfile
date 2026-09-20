# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
FROM --platform=linux/arm64/v8 python:3.11-slim-bookworm@sha256:bbc491ed39611eede47b1058ad4afb9ea957fb3bf4a7f1b442c6a5628ab93bdc

LABEL org.opencontainers.image.title="Racecraft coding grader" \
      org.opencontainers.image.description="Standard-library-only, network-disabled coding grader"

COPY --chmod=0555 grade.py /opt/racecraft/grade.py
COPY --chmod=0555 qualify.py /opt/racecraft/qualify.py

USER 65532:65532
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/python", "-I", "-B", "/opt/racecraft/grade.py"]
