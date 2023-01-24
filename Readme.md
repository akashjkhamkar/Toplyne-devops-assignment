# Topylne Dev Ops task

### Goal -

Create an ASG in a private subnet with each instance having a nginx container and a load balancer in public subnet which targets the ASG

### Solution -

ECS !

Creating an ECS cluster and attaching an autoscaling group to it with DEAMON configuration so that with each new instance, a container will surely spin up inside it

### Pulumi

Wrote the code using python, used pulumi_aws and pulumi_awsx. pulumi_awsx helped alot in creating private subnets easily by auto creating NAT and route tables.