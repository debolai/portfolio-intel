variable "env" {
  type    = string
  default = "dev"
}
variable "region" {
  type    = string
  default = "ca-central-1"
}
variable "alert_email" {
  type        = string
  description = "Receives the AWS Budgets forecast alert"
}
variable "domain_name" {
  type        = string
  default     = ""
  description = "e.g. pi.example.com. Empty: no hosted zone or certificate; the ALB serves HTTP only."
}
variable "monthly_budget_usd" {
  type    = string
  default = "50"
}
variable "audit_retention_days" {
  type    = number
  default = 365
}
