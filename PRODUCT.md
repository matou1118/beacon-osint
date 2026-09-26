# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Existing Python/Streamlit application; the workbench extends the current `app.py` without replacing the collection or rules pipeline.

## Users

The primary user is the project operator reviewing source health, collection failures, extraction quality, backups, and approved public information. Secondary users are H5 readers who will consume the API and follow subscriptions.

## Product Purpose

Provide a traceable public-information screening and alerting system. Success means an operator can quickly see whether the pipeline is healthy, identify failures, inspect matched announcements, and act without changing source or matching policy from the UI.

## Positioning

A compliance-first operations workbench for approved public sources: deterministic rules, visible evidence, and reversible operational actions rather than open-web monitoring or opaque automation.

## Operating Context

The workbench runs locally against SQLite, source configuration, scheduler output, quality reports, failure reports, and backups. The H5 API is a separate public-facing surface. The operator uses desktop or tablet frequently and may check urgent status from a phone.

## Capabilities and Constraints

- Show source health, collection failures, extraction success, match volume, backup status, and recent announcements.
- Preserve existing source whitelists, robots rules, retention policy, and deterministic matching.
- Do not expose raw secrets, cookies, tokens, or unrestricted database editing.
- Treat API authentication and external providers as explicit configuration; no fake production login.

## Brand Commitments

Name: 灯塔 OSINT / Beacon OSINT. Tone: calm, precise, operational, and evidence-led. The interface should feel like a reliable instrument rather than a marketing dashboard.

## Evidence on Hand

- Existing Streamlit entry: `app.py`.
- Existing SQLite models: `storage/models.py` and `storage/db.py`.
- Existing quality and scheduler outputs: `quality.py` and `scheduler.py`.
- Existing API: `api/`.
- No existing visual identity, logo, or approved admin template has been supplied.

## Product Principles

1. Show system state before showing volume.
2. Keep every operational number traceable to a source or report.
3. Make the safe action the obvious action.
4. Preserve context when an operator moves from overview to detail.
5. Prefer a small, dependable workbench over a feature-heavy admin suite.

## Accessibility & Inclusion

Keyboard-accessible controls, visible focus states, readable contrast, meaningful empty/error states, and a layout that remains usable at phone width are required.
