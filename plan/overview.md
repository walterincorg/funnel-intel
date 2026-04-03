# Funnel Traversal Overview

## What This System Is For

The funnel traversal system is the part of the platform that walks through a competitor's marketing funnel like a real user and turns that journey into structured intelligence. Its job is to capture the actual sequence of questions, answer options, chosen actions, pricing or discount details, and the point where the funnel ends or becomes gated. That gives the business a repeatable way to compare how competitor funnels change over time without relying on manual checks.

## What Happens During a Funnel Run

1. An operator or scheduler starts a funnel scan for a competitor.
2. The dashboard enqueues a scan job and the worker picks it up.
3. The  traversal engine opens the competitor's funnel URL and moves through the funnel step by step.
4. each meaningful step is recording what was shown and what action advanced the journey.
5. The app normalizes the full journey into one shared data model.
6. Supabase stores the run, the step history, the summary snapshot, and the related HTML or screenshot artifacts.
7. Later runs can be compared against earlier ones to detect changes in messaging, flow, pricing, or gating.

The important that the journey is still normalized and stored by this app in one consistent format.

## Why The Architecture Matters

- Async scan jobs make traversal runs inspectable instead of blocking the dashboard.
- Stored artifacts make it possible to debug failures and explain what happened during a run.

In short: the platform is designed so the execution layer can evolve, while the stored funnel intelligence stays consistent.