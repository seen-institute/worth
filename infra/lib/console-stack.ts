import { readFileSync } from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { CfnOutput, SecretValue, Stack, type StackProps } from "aws-cdk-lib";
import * as amplify from "aws-cdk-lib/aws-amplify";
import type { Construct } from "constructs";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

export interface WorthConsoleProps extends StackProps {
  /** The App Runner hostname the console's /api/* requests are proxied to. */
  readonly apiHost: string;
  /** The GitHub repository Amplify builds from. */
  readonly repository?: string;
  /** The branch Amplify builds. */
  readonly branch?: string;
  /**
   * Secrets Manager name of a GitHub personal access token with `repo` scope,
   * created out of band. Amplify needs it once, to install its webhook.
   */
  readonly githubTokenSecret?: string;
}

/**
 * The console on Amplify Hosting, with the one rewrite rule that makes it a
 * full-stack app: `/api/<*>` is proxied, status 200, to worth-api. Same
 * origin, so no CORS, and the app never learns a host name.
 *
 * Deep links need no rule because routing is hash-based.
 */
export class WorthConsoleStack extends Stack {
  constructor(scope: Construct, id: string, props: WorthConsoleProps) {
    super(scope, id, props);

    const repository = props.repository ?? "https://github.com/seen-institute/worth";
    const branch = props.branch ?? "main";
    const tokenSecret = props.githubTokenSecret ?? "worth/github-token";

    const app = new amplify.CfnApp(this, "Console", {
      name: "worth-console",
      repository,
      // Resolved by CloudFormation at deploy time; the token is never in the template.
      accessToken: SecretValue.secretsManager(tokenSecret).toString(),
      platform: "WEB",
      buildSpec: readFileSync(path.join(REPO_ROOT, "amplify.yml"), "utf8"),
      environmentVariables: [
        { name: "AMPLIFY_MONOREPO_APP_ROOT", value: "app" },
        { name: "VITE_API_BASE", value: "/api" },
      ],
      customRules: [
        {
          source: "/api/<*>",
          target: `https://${props.apiHost}/api/<*>`,
          status: "200",
        },
      ],
    });

    new amplify.CfnBranch(this, "Branch", {
      appId: app.attrAppId,
      branchName: branch,
      enableAutoBuild: true,
      stage: "PRODUCTION",
    });

    new CfnOutput(this, "ConsoleUrl", {
      value: `https://${branch}.${app.attrDefaultDomain}`,
    });
  }
}
