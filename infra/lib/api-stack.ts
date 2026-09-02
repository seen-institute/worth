import * as path from "node:path";
import { fileURLToPath } from "node:url";

import * as apprunner from "@aws-cdk/aws-apprunner-alpha";
import { CfnOutput, Duration, RemovalPolicy, Stack, type StackProps } from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecrAssets from "aws-cdk-lib/aws-ecr-assets";
import * as rds from "aws-cdk-lib/aws-rds";
import type { Construct } from "constructs";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

export interface WorthApiProps extends StackProps {
  /** The institution's Medicare locality the pipeline prices in. */
  readonly locality?: string;
  /** `facility` or `non-facility`. */
  readonly setting?: string;
}

/**
 * The console's backend: worth-api on App Runner, Postgres on RDS.
 *
 * This is the public demonstration stack, on synthetic data. It is not the
 * registry deployment: that runs inside a BAA environment with private
 * networking, KMS, access logging and an identity in front of it, none of
 * which is here. What carries over is the container. The image is built from
 * the repository's own Dockerfile at deploy time, with the CMS archives fetched
 * and hash-verified during the build, so the running service needs no network
 * beyond its database.
 */
export class WorthApiStack extends Stack {
  /** The App Runner hostname, without a scheme. */
  readonly serviceHost: string;

  constructor(scope: Construct, id: string, props: WorthApiProps = {}) {
    super(scope, id, props);

    // Two isolated subnets and nothing else. The database never needs to reach
    // out, and the service reaches it through a VPC connector; ECR pulls and
    // inbound traffic go through App Runner's own network, not this VPC. No
    // NAT gateway, which is the single largest line on a stack like this.
    const vpc = new ec2.Vpc(this, "Vpc", {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        { name: "isolated", subnetType: ec2.SubnetType.PRIVATE_ISOLATED, cidrMask: 24 },
      ],
    });

    // Postgres 16, pinned to match compose.yaml: the point of the repository is
    // that everyone gets the same answer, and that includes the server the SQL
    // runs on. The schema and the fee schedule are applied by the API on boot,
    // so the database starts empty and is rebuildable from public CMS data.
    const db = new rds.DatabaseInstance(this, "Db", {
      engine: rds.DatabaseInstanceEngine.postgres({ version: rds.PostgresEngineVersion.VER_16 }),
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.T4G, ec2.InstanceSize.MICRO),
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      databaseName: "worth",
      credentials: rds.Credentials.fromGeneratedSecret("worth"),
      allocatedStorage: 20,
      storageEncrypted: true,
      publiclyAccessible: false,
      backupRetention: Duration.days(7),
      // Demonstration data only: everything in it is public CMS reference
      // data the API reloads on its next boot. Nothing is lost with the stack.
      deletionProtection: false,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    const secret = db.secret;
    if (!secret) throw new Error("the database credentials should have been generated");

    const connector = new apprunner.VpcConnector(this, "VpcConnector", {
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
    });
    db.connections.allowDefaultPortFrom(connector, "worth-api on App Runner");

    const image = new ecrAssets.DockerImageAsset(this, "ApiImage", {
      directory: REPO_ROOT,
      file: "packages/worth-api/Dockerfile",
      platform: ecrAssets.Platform.LINUX_AMD64,
    });

    const service = new apprunner.Service(this, "Api", {
      source: apprunner.Source.fromAsset({
        asset: image,
        imageConfiguration: {
          port: 8000,
          environmentVariables: {
            PGHOST: db.dbInstanceEndpointAddress,
            PGPORT: db.dbInstanceEndpointPort,
            PGDATABASE: "worth",
            PGUSER: "worth",
            WORTH_LOCALITY: props.locality ?? "NY01",
            WORTH_SETTING: props.setting ?? "facility",
          },
          // The password never appears in a template or a console: App Runner
          // reads it from Secrets Manager at start and the API assembles the URL.
          environmentSecrets: {
            PGPASSWORD: apprunner.Secret.fromSecretsManager(secret, "password"),
          },
        },
      }),
      vpcConnector: connector,
      cpu: apprunner.Cpu.ONE_VCPU,
      memory: apprunner.Memory.TWO_GB,
      // Boot loads four fee-schedule vintages into Postgres the first time,
      // which takes longer than a default health check allows.
      healthCheck: apprunner.HealthCheck.http({
        path: "/api/health",
        interval: Duration.seconds(10),
        timeout: Duration.seconds(5),
        healthyThreshold: 1,
        unhealthyThreshold: 10,
      }),
      autoDeploymentsEnabled: false,
    });

    this.serviceHost = service.serviceUrl;

    new CfnOutput(this, "ApiUrl", {
      value: `https://${service.serviceUrl}`,
      description: "worth-api. Point the Amplify rewrite for /api/<*> here.",
    });
    new CfnOutput(this, "DatabaseSecretArn", { value: secret.secretArn });
  }
}
