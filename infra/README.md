# infra

The AWS stacks behind the console, as CDK.

| stack | what | deployed by |
|---|---|---|
| `WorthApi` | worth-api on App Runner, Postgres 16 on RDS in two isolated subnets, a VPC connector between them, the database secret | the Deploy workflow, on every push to `main` |
| `WorthConsole` | the Amplify app for `app/`, with the `/api/<*>` rewrite to the API and the `main` branch on auto-build | by hand, once |

```bash
just infra-synth      # assemble the templates; no credentials needed
just infra-deploy     # cdk deploy WorthApi; needs credentials and Docker
```

## What deploying the API does

`cdk deploy WorthApi` builds the image from `packages/worth-api/Dockerfile` on
the deploying machine, fetching and hash-verifying the four pinned CMS archives
during the build, pushes it to the CDK assets repository in ECR, and rolls it
out on App Runner. The database starts empty; on first boot the API applies
`worth-fees`' schema and loads every vintage, which is why the health check
tolerates ten failures before giving up.

The database password is never in a template, a log or the console. App Runner
reads it from the RDS-generated secret at start into `PGPASSWORD`, alongside
plain `PGHOST`, `PGPORT`, `PGDATABASE` and `PGUSER`, and the API assembles the
connection URL from those.

Context values: `-c locality=NY01`, `-c setting=facility`.

## Deploying the console

Once, with a GitHub personal access token that has `repo` scope:

```bash
aws secretsmanager create-secret --name worth/github-token --secret-string "$GITHUB_TOKEN"
npx --prefix infra cdk deploy WorthConsole
```

Amplify uses the token to install its webhook and then builds `main` on every
push. The rewrite rule and the environment variables are in the stack, so the
Amplify console needs nothing typed into it. If the app already exists from a
manual setup, do not deploy this stack; add the rule from the `ApiUrl` output
under App settings, Rewrites and redirects, instead.

## What this is not

This is the public demonstration stack, on synthetic data. It has no
authentication, its API is reachable from the internet, and its network is a
minimum. The registry deployment the partner brief describes, a BAA environment
with private networking, KMS, access logging and an identity in front of
everything, is a separate stack. What carries over to it is the container.
