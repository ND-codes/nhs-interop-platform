# Diagrams

The diagrams below are Mermaid, so GitHub renders them from this file and they stay
reviewable in a pull request. `docs/architecture.svg` is the exported picture used in
the README and in the blog series.

Everything here describes the local Docker Compose stack, which is what `make demo`
starts. The AWS topology is in [architecture.md](architecture.md).

## Services and message flow

```mermaid
flowchart LR
    trust["Trust integration engine<br/>sends HL7 v2 over HTTP"]
    ingest["ingest :8000<br/>POST /hl7"]
    transform["transform :8001<br/>POST /transform"]
    pds["pds-client :8002<br/>GET /pds/{nhs_number}"]
    sandbox["NHS PDS FHIR sandbox<br/>only when PDS_STUB=false"]
    hapi["HAPI FHIR :8080<br/>POST /fhir"]
    pg[("Postgres")]
    dlq[/"dead-letter folder<br/>DLQ_PATH"/]

    trust -->|"HL7 v2 text"| ingest
    ingest -->|"HTTP, X-Trace-Id"| transform
    transform -->|"transaction Bundle"| hapi
    hapi --> pg
    transform <-->|"enrich, fail open"| pds
    pds -.->|"HTTPS"| sandbox
    transform -.->|"mapping or write fails"| dlq
```

`ingest` calls `transform` over synchronous HTTP. There is no queue yet: an SQS FIFO
queue between them is the first item on the roadmap.

## Monitoring

```mermaid
flowchart LR
    subgraph services["services, each exposing /metrics"]
        direction TB
        ingest["ingest"]
        transform["transform"]
        pds["pds-client"]
    end
    prom["Prometheus :9090<br/>scrapes every 10s"]
    graf["Grafana :3000<br/>HL7 pipeline dashboard"]
    rules["5 alert rules<br/>alerts.yml"]

    services --> prom
    prom --> graf
    prom --> rules
```

## One admission, end to end

```mermaid
sequenceDiagram
    autonumber
    participant T as Trust engine
    participant I as ingest
    participant X as transform
    participant P as pds-client
    participant F as HAPI FHIR

    T->>I: POST /hl7 (ADT^A01)
    I->>I: check the MSH segment, mint a trace ID
    I->>X: POST /transform, X-Trace-Id
    X->>X: map HL7 v2 to a FHIR R4 transaction Bundle
    X->>P: GET /pds/9000000009
    alt PDS answers
        P-->>X: Patient demographics
        X->>X: overlay name, gender, birthDate, address, telecom
    else PDS fails or the breaker is open
        P--xX: error
        X->>X: keep the demographics from the message
    end
    X->>F: POST /fhir (transaction Bundle)
    alt the write succeeds
        F-->>X: 200, Bundle of responses
        X-->>I: 200, trace_id and entry count
    else the write keeps failing
        F--xX: 5xx, after three attempts
        X->>X: write the raw message to the dead-letter folder, count it
        X-->>I: 502
    end
    I-->>T: 200, message_id, trace_id, forwarded
```

The PDS branch is the fail-open rule: a lookup that fails degrades the data rather
than stopping the admission being recorded. The write branch is the fail-closed rule:
a Bundle that cannot be stored is kept for replay instead of being dropped.

`ingest` answers 200 with `forwarded: false` when `transform` cannot be reached, and
nothing retries the message. That is the gap the queue closes.

## Alerting

```mermaid
flowchart TD
    m1["http_request_duration_seconds<br/>from transform"] --> a1["TransformLatencyHigh<br/>p95 > 1s for 10m"]
    m2["http_requests_total<br/>from ingest, /hl7"] --> a2["IngestNoTraffic<br/>no messages for 15m"]
    m3["http_requests_total 5xx<br/>from transform"] --> a3["TransformErrorRateHigh<br/>> 2% for 5m"]
    m4["hl7_dlq_total"] --> a4["DLQRateHigh<br/>> 0.1/s for 5m"]
    m5["pybreaker_state{name=pds}<br/>not exported yet"] -.-> a5["PDSCircuitOpen<br/>cannot fire yet"]

    a1 --> rb["docs/runbook.md"]
    a2 --> rb
    a3 --> rb
    a4 --> rb
    a5 -.-> rb
```

The rules are in [observability/prometheus/alerts.yml](../observability/prometheus/alerts.yml).
`PDSCircuitOpen` is drawn with a dashed line because no service exports
`pybreaker_state` yet, so the rule cannot fire. Exporting the breaker state is
tracked on the roadmap.
