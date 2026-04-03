# Funnel Traversal Hosting And Access

## Where The System Is Hosted

### Ubuntu VPS

The current staging environment runs on an Ubuntu VPS. This machine is responsible for:

- the private dashboard UI
- the background worker
- scan job orchestration
- the local Playwright browser runtime
- the OpenClaw gateway used by the app

Current staging host, the ssh access is configured, so you can just ssh into the server:

- `187.124.241.54`



### Supabase

Supabase is the system of record for funnel traversal data.

It also stores the private HTML and screenshot artifacts used for debugging and later comparison.



## How Operators Access It

The dashboard should intentionally not exposed on the public internet. It should be loopback-bound on the VPS and accessed through a private access layer.

future access pattern:

- SSH tunnel to the VPS
- then open the local forwarded dashboard URL

Example:

```bash
ssh -L 4318:127.0.0.1:4318 root@187.124.241.54
```

Then open:

- `http://127.0.0.1:4318`



## What Runs On The VPS

The current services are:

- `openclaw-gateway.service`

New services should be created, these should provide:

- dashboard access
- queue processing
- funnel orchestration

