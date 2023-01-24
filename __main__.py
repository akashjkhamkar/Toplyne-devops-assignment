"""Deploys ECS with EC2 container instances (as opposed to using Fargate)."""
import json
import pulumi
import pulumi_aws as aws
import pulumi_awsx as awsx

# Create a new VPC and subnets
vpc = awsx.ec2.Vpc("custom", subnet_specs=[
    awsx.ec2.SubnetSpecArgs(
        name='custom_private_1',
        type=awsx.ec2.SubnetType.PRIVATE,
        cidr_mask=24,
    ),
    awsx.ec2.SubnetSpecArgs(
        name='custom_private_2',
        type=awsx.ec2.SubnetType.PRIVATE,
        cidr_mask=24,
    ),
    awsx.ec2.SubnetSpecArgs(
        name='custom_public_1',
        type=awsx.ec2.SubnetType.PUBLIC,
        cidr_mask=24,
    )
])

pulumi.export("vpc_id", vpc.vpc_id)
pulumi.export("publicSubnetIds", vpc.public_subnet_ids)
pulumi.export("privateSubnetIds", vpc.private_subnet_ids)

# Security group to access the nginx container.
sg = aws.ec2.SecurityGroup(
    "nginx-sg",
    description="Allow HTTP",
    vpc_id=vpc.vpc_id,
    ingress=[
        aws.ec2.SecurityGroupIngressArgs(protocol="tcp", from_port=80, to_port=80, cidr_blocks=["0.0.0.0/0"])
    ],
    egress=[
        aws.ec2.SecurityGroupEgressArgs(protocol=-1, from_port=0, to_port=0, cidr_blocks=["0.0.0.0/0"])
    ]
)

# IAM role for:
# Task Role: to allow the TaskDefinition to launch tasks on the cluster. 
task_execution_role = aws.iam.Role(
    "task-execution-role",
    assume_role_policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement":
            [
                {
                    "Sid": "",
                    "Effect": "Allow",
                    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }
            ],
        }
    ),
)

task_execution_role_policy_attach = aws.iam.RolePolicyAttachment(
    "task-excution-policy-attach",
    role=task_execution_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
)

# Instance IAM profile: to allow the EC2 instances permission to join the ECS cluster.
ecs_instance_role = aws.iam.Role(
    "ecs-instance-role",
    assume_role_policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement":
            [
                {
                    "Sid": "",
                    "Effect": "Allow",
                    "Principal": {"Service": "ec2.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }
            ],
        }
    ),
)

ecs_instance_role_policy_attach = aws.iam.RolePolicyAttachment(
    "ecs-instance-policy-attach",
    role=ecs_instance_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
)

ecs_instance_role_policy_attach_2 = aws.iam.RolePolicyAttachment(
    "ecs-instance-policy-attach2",
    role=ecs_instance_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AmazonEC2RoleforSSM"
)

ecs_instance_profile = aws.iam.InstanceProfile("ecs-iam-instance-profile", role=ecs_instance_role.name)

# Find an "ECS optimized" AMI to use for the EC2 container instances.
ecs_instance_ami = aws.ec2.get_ami(
    most_recent="true",
    owners=["amazon"],
    filters=[
        {
            "name": "name",
            "values": ["amzn2-ami-ecs-hvm-*-x86_64-*"]  
        }
    ]
)

# Env variables for the instances, so that they can join the cluster
cluster_name = "my-ecs-cluster"
user_data='''#!/bin/bash
echo ECS_CLUSTER={cluster_nm} >> /etc/ecs/ecs.config && 
echo ECS_ENABLE_TASK_IAM_ROLE=true >> /etc/ecs/ecs.config'''.format(cluster_nm=cluster_name)

# Launch configuration
launch_config = aws.ec2.LaunchConfiguration(
    "launch-config",
    image_id=ecs_instance_ami.id,
    instance_type="t2.micro",
    iam_instance_profile=ecs_instance_profile.name,
    user_data=user_data,
)

# Autoscaling group
auto_scaling = aws.autoscaling.Group(
    "auto-scaling",
    vpc_zone_identifiers=[vpc.private_subnet_ids[0]],
    launch_configuration=launch_config.name,
    min_size=3,
    max_size=10,
    protect_from_scale_in=False
)

capacityprovider = aws.ecs.CapacityProvider(
    "capacity-provider",
    auto_scaling_group_provider=aws.ecs.CapacityProviderAutoScalingGroupProviderArgs(
        auto_scaling_group_arn=auto_scaling.arn,
        managed_termination_protection="DISABLED",
        managed_scaling=aws.ecs.CapacityProviderAutoScalingGroupProviderManagedScalingArgs(
            status="DISABLED"
        )
    )
)

# Esc cluster, providing it with capacity providers
cluster = aws.ecs.Cluster(
    "cluster",
    name=cluster_name,
    capacity_providers=[capacityprovider.name],
)

# Application load balancer in the public subnet
load_balancer = aws.lb.LoadBalancer(
    "load-balancer", 
    load_balancer_type="application", 
    security_groups=[sg.id],
    subnets=vpc.public_subnet_ids,
    internal=False,
)

# Creating a target group to forward the traffic
atg = aws.lb.TargetGroup(
    "app-tg",
	port=80,
	protocol="HTTP",
	target_type="ip",
	vpc_id=vpc.vpc_id,
)

# Listener to forward the traffic to the target group
wl = aws.lb.Listener(
    "web",
	load_balancer_arn=load_balancer.arn,
	port=80,
	default_actions=[aws.lb.ListenerDefaultActionArgs(type="forward", target_group_arn=atg.arn)]
)

# Task definition for creating our containers.
task_def = aws.ecs.TaskDefinition(
    "my-app",
    family="ec2-task-definition",
    cpu="256",
    memory="512",
    network_mode="awsvpc",
    requires_compatibilities=["EC2"],
    execution_role_arn=task_execution_role.arn,
    container_definitions=json.dumps([{
		"name": "my-app",
		"image": "nginx",
		"portMappings": [{
			"containerPort": 80,
			"hostPort": 80,
			"protocol": "tcp"
		}]
	}]),
    opts=pulumi.ResourceOptions(depends_on=[cluster])
)

# ECS Service
service = aws.ecs.Service(
    "my-task-runner",
    scheduling_strategy='DAEMON',
    cluster=cluster.arn,
    launch_type="EC2",
    task_definition=task_def.arn,
    network_configuration=aws.ecs.ServiceNetworkConfigurationArgs(
		assign_public_ip=False,
		subnets=[vpc.private_subnet_ids[0]],
		security_groups=[sg.id],
	),
    load_balancers=[aws.ecs.ServiceLoadBalancerArgs(
		target_group_arn=atg.arn,
		container_name="my-app",
		container_port=80,
	)],
    opts=pulumi.ResourceOptions(depends_on=[wl])
)

pulumi.export("Load Balancer DNS : ", pulumi.Output.concat("http://", load_balancer.dns_name))
pulumi.export("NOTE", "Containers might take some time to spin up.")