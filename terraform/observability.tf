###############################################################################
# CloudWatch — logs + alarms
#
# The CloudWatch agent installed in user-data ships the EC2's docker container
# stdout/stderr to this log group. Three alarms cover the failure modes we
# actually want to know about during an active campaign:
#
#   1. RDS CPU sustained above 80% — the settlement_analytics recompute is
#      runaway-querying or the sync is being hit too frequently.
#   2. ALB returning 5xx — app is throwing.
#   3. Sync hasn't run successfully in 24 hours (delivered via the custom
#      metric the next iteration of the sync service will push).
#
# Alarms fire to an SNS topic the operator subscribes to with their email.
###############################################################################

resource "aws_cloudwatch_log_group" "app" {
  name              = "/mda-dashboard/app"
  retention_in_days = 30 # cost: $0.03/GB-month after 30 days; 30 is the sweet spot
}

resource "aws_cloudwatch_log_group" "ec2_system" {
  name              = "/mda-dashboard/ec2-system"
  retention_in_days = 14
}

# ── SNS for alarms ───────────────────────────────────────────────────────────

resource "aws_sns_topic" "alarms" {
  name = "${var.project_name}-alarms"
}

# Operator subscribes to this topic via the AWS console:
#   aws sns subscribe --topic-arn <arn> --protocol email --notification-endpoint you@example.com

# ── RDS CPU alarm ────────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "rds_cpu_high" {
  alarm_name          = "${var.project_name}-rds-cpu-high"
  alarm_description   = "RDS CPU sustained above 80% for 10 min — settlement recompute or runaway query."
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 80
  period              = 300
  evaluation_periods  = 2
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.id
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]
}

# ── RDS free storage alarm ───────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "rds_free_storage_low" {
  alarm_name          = "${var.project_name}-rds-free-storage-low"
  alarm_description   = "RDS free storage below 5 GB — storage autoscaling should have triggered."
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  statistic           = "Average"
  comparison_operator = "LessThanThreshold"
  threshold           = 5 * 1024 * 1024 * 1024 # 5 GB in bytes
  period              = 300
  evaluation_periods  = 2
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.id
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
}

# ── ALB 5xx alarm ────────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name          = "${var.project_name}-alb-5xx"
  alarm_description   = "More than 10 HTTP 5xx responses in 5 minutes — app is throwing."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  statistic           = "Sum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 10
  period              = 300
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
}

# ── EC2 instance status alarm ────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "ec2_status" {
  alarm_name          = "${var.project_name}-ec2-status"
  alarm_description   = "EC2 instance is failing AWS health checks."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed"
  statistic           = "Maximum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  period              = 60
  evaluation_periods  = 2
  treat_missing_data  = "notBreaching"

  dimensions = {
    InstanceId = aws_instance.app.id
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
}
