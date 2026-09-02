import { App } from "aws-cdk-lib";

import { WorthApiStack } from "../lib/api-stack.js";
import { WorthConsoleStack } from "../lib/console-stack.js";

const app = new App();

// Account and region come from the deploying credentials. Left unset, the
// stacks synthesise environment-agnostic, which is what CI checks.
const env =
  process.env.CDK_DEFAULT_ACCOUNT && process.env.CDK_DEFAULT_REGION
    ? { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION }
    : undefined;

const api = new WorthApiStack(app, "WorthApi", {
  env,
  locality: app.node.tryGetContext("locality"),
  setting: app.node.tryGetContext("setting"),
});

// Deployed separately, and only once a GitHub token is in Secrets Manager:
//   npx cdk deploy WorthConsole
new WorthConsoleStack(app, "WorthConsole", {
  env,
  apiHost: api.serviceHost,
  repository: app.node.tryGetContext("repository"),
  branch: app.node.tryGetContext("branch"),
  githubTokenSecret: app.node.tryGetContext("githubTokenSecret"),
});
