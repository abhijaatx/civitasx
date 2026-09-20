# CivitasX

## What we are making

CivitasX is a local-first civic network for Bengaluru residents. It combines a public civic issue feed with a private, Hermes-style agent that helps people research official records, understand evidence, prepare complaint drafts, and track what happens next.

## AWS architecture

The Phase 2 AWS boundary is defined in [`infra/template.yaml`](infra/template.yaml):

```mermaid
flowchart TB
    Resident[Resident browser]

    subgraph Edge[Public edge]
        CloudFront[Amazon CloudFront\nHTTPS SPA delivery]
        WebBucket[(Private S3\nfrontend bucket)]
    end

    subgraph Identity[Identity]
        Cognito[Amazon Cognito\nUser Pool + OAuth client]
    end

    subgraph Runtime[Application runtime]
        Api[Lambda Web Adapter\nFastAPI Function URL]
        Logs[CloudWatch Logs]
        Role[IAM runtime role]
    end

    subgraph Data[Private application data]
        Table[(DynamoDB\nowner-scoped table)]
        Artifacts[(S3\nencrypted artifacts)]
        Corpus[(S3\nversioned source corpus)]
    end

    Bedrock[Amazon Bedrock\nNova + Titan embeddings]

    subgraph Guardrails[Operational guardrails]
        Budget[AWS Budgets]
        SNS[Amazon SNS\noptional email alert]
    end

    Resident -->|HTTPS| CloudFront
    Resident -->|Sign in / tokens| Cognito
    Resident -->|API requests| Api
    CloudFront -->|Origin Access Control| WebBucket
    Cognito -->|Bearer tokens| Api
    Api --> Role
    Role --> Table
    Role --> Artifacts
    Role --> Corpus
    Role --> Bedrock
    Api --> Logs
    Budget --> SNS

    classDef aws fill:#fff3e0,stroke:#d97706,color:#111827
    classDef data fill:#e0f2fe,stroke:#0284c7,color:#111827
    classDef edge fill:#ede9fe,stroke:#7c3aed,color:#111827
    class CloudFront,Cognito,Api,Logs,Role,Bedrock,Budget,SNS aws
    class Table,Artifacts,Corpus,WebBucket data
    class Resident edge
```

The frontend bucket, artifacts bucket, and source-corpus bucket remain private. The application uses Cognito for production identity, DynamoDB for owner-scoped state, S3 for artifacts and versioned source documents, and Bedrock only when the configured cloud provider is enabled.

## What we intend to achieve

We intend to make civic information easier to understand and act on while keeping evidence grounded, personal work private, and every public or external action reviewable. The project aims to grow into a trustworthy civic workspace where residents can move from a question to a clear, accountable next step.
