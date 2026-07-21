# Atlas Architecture

This document provides a high-level overview of the Atlas architectural layout and system design.

## Core Stack
- **Backend:** Django (Python) providing both the web server and business logic.
- **Database:** MySQL as the primary persistent data store.
- **Frontend:** Server-side rendered HTML via Django Templates, styled with custom CSS focusing on a clean, modern SaaS aesthetic.

## Security & Permission Model
Atlas utilizes Django's built-in authentication system with custom permission validation logic in the functional views.
- **Read-Only / UI State:** Users without permissions cannot see restricted navigation links or action buttons.
- **Strict Editing Window:** Standard data entrants can create records but can only modify them if they are the author AND the record was created within the last 5 minutes.
- **Soft Errors:** Permission denials redirect users gracefully to safe zones (like the dashboard) with non-intrusive warning messages rather than hard HTTP 403 pages.

## Integration & ETL Pipeline (Planned)
To elevate Atlas from a manual entry tool to a real-time monitor, the application utilizes a dedicated `etl` app for integrations:
- **Kafka Consumers:** For streaming data ingestion from other monitoring tools or databases.
- **REST APIs:** Postman/REST integrations for pulling daily/weekly summary reports from external infrastructure APIs.
- **Data Standardization:** Incoming payloads are normalized and pushed to the standard core database models (Incident, Replication Monitor, etc.) utilizing Django ORM.

## Directory Structure
```
windsurf-project/
├── backend/
│   ├── api/             # Core functional views, forms, and models
│   ├── dba_tracker/     # Main Django settings and routing
│   ├── etl/             # Integration logic (Kafka, REST APIs)
│   ├── users/           # Custom authentication logic
│   ├── docs/            # Project documentation
│   └── templates/       # HTML view templates
```
