# Architecture

EvalScope is the primary evaluation application and LM Studio is the only inference server.
The project adds a thin policy and reproducibility layer; it does not add a second model
server or a replacement dashboard.

```text
local-evals CLI / native EvalScope dashboard
              |
              | stateless OpenAI-compatible requests
              v
verified 127.0.0.1 LM Studio origin
              |
              v
one verified local model instance on this Mac

external state directory: datasets, raw outputs, settings, private reports
public Git checkout: source, synthetic tests, factual references, reviewed aggregates
```

Management operations use LM Studio's native `/api/v1` namespace; evaluation uses the
OpenAI-compatible `/v1` base. These paths are not concatenated. A loopback listener is not
by itself proof of local execution because LM Link can route work to another device. The
locality gate therefore records link state, instance identity, engine/runtime evidence, and
a successful request before a run is eligible as a local measurement.

Public CI has no route into this diagram. It runs mock HTTP/SSE servers bound to loopback
inside disposable GitHub-hosted runners. Generated code grading, when separately enabled
locally, occurs in a disposable network-disabled container; inference orchestration remains
on the host.
