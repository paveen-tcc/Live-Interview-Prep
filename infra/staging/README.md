# Staging bootstrap contract

> **Deferred:** Local development with Neon PostgreSQL is the current product
> scope. These Azure instructions are retained for a future deployment phase and
> should not be executed yet.

M1 targets Azure Container Apps with built-in authentication backed by a
Microsoft Entra External ID customer tenant, Azure Container Registry, and Azure
Database for PostgreSQL. Users enter an email address and receive a one-time
passcode; they do not create a password or need a Microsoft account. GitHub
Actions owns all repeat deployments after a one-time infrastructure bootstrap;
no files are copied to staging manually.

## One-time Azure prerequisites

Create or select:

1. A resource group and Container Apps environment in the approved Azure region.
2. An Azure Container Registry with a managed identity granted `AcrPull`.
3. A Container App with external HTTPS ingress targeting port `8000`.
4. Azure Database for PostgreSQL and an application database/user.
5. A Microsoft Entra External ID external tenant with email one-time passcode
   enabled as the local-account sign-in method.
6. A sign-up and sign-in user flow that uses email one-time passcode and collects
   display name and email claims.
7. An application registration in the external tenant, associated with that user
   flow, for Container Apps Easy Auth.
8. A GitHub workload-identity federation allowed to deploy to the resource group.

The region, subscription, naming convention, network topology, and PostgreSQL
retention tier remain operator decisions; this repository does not guess them.

## Container App configuration

Store the PostgreSQL connection string as the Container App secret
`database-url`. Configure these runtime variables:

```text
APP_ENV=staging
AUTH_MODE=easy_auth
AUTO_CREATE_SCHEMA=false
DATABASE_URL=secretref:database-url
LOG_LEVEL=INFO
WEB_DIST_DIR=/app/web/dist
ENABLE_TEXT_DEV_MODE=false
```

The container runs `alembic upgrade head` before starting Uvicorn. Configure the
liveness path as `/api/health/live` and readiness path as `/api/health/ready`.

In the external tenant, select **Email with one-time passcode** rather than
**Email with password** for the user flow. Associate the application registration
with that flow and include stable subject, email, and display-name claims.

Enable Container Apps built-in Microsoft Entra authentication using that external
tenant and application registration. Allow anonymous requests at the platform
boundary so the React passwordless sign-in screen and health probes load; the
FastAPI API routes independently require and validate the trusted
`X-MS-CLIENT-PRINCIPAL*` headers. Container Apps removes externally supplied
identity headers before forwarding authenticated identity data.

The Entra redirect URI is:

```text
https://<container-app-fqdn>/.auth/login/aad/callback
```

## GitHub staging environment

Create a protected GitHub environment named `staging` with secrets:

```text
AZURE_CLIENT_ID
AZURE_TENANT_ID
AZURE_SUBSCRIPTION_ID
```

Add repository or environment variables:

```text
AZURE_RESOURCE_GROUP
AZURE_ACR_NAME
AZURE_CONTAINER_APP_NAME
STAGING_APP_URL
```

Once these values and the pre-provisioned resources exist,
`.github/workflows/deploy-staging.yml` builds the single Dockerfile, publishes a
versioned Container Apps revision, and verifies database readiness from CI.
