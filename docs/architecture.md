# How the local system works

The local evaluation tools send tasks to a verified LM Studio model instance, check the answers,
and retain detailed evidence outside Git. Only reviewed summaries reach this static public site.
The website does not send model requests or connect to the test computer.

<section class="reader-section cool" aria-label="Local request path">

## From a task to private evidence

```text
local-evals CLI
              |
              | native /api/v1/chat requests
              v
verified 127.0.0.1 LM Studio origin
              |
              v
one verified local model instance on this Mac

external state directory: datasets, raw outputs, settings, private reports
public Git checkout: source, synthetic tests, factual references, reviewed aggregates
```

</section>

The implemented chat client uses LM Studio's native `/api/v1/chat` route; it is not an
OpenAI-compatible `/v1` chat request. A loopback listener is not
by itself proof of local execution because LM Link can route work to another device. The
locality gate therefore records link state, instance identity, engine/runtime evidence, and
a successful request before a run is eligible as a local measurement.

<section class="reader-section warm" aria-label="Public automation boundary">

## What public automation can access

Public CI has no route into this diagram. It runs mock HTTP/SSE servers bound to loopback
inside disposable GitHub-hosted runners. Generated code grading, when separately enabled
locally, occurs in a disposable network-disabled container; inference orchestration remains
on the host.

</section>
