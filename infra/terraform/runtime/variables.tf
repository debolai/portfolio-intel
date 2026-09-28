variable "env" {
  type    = string
  default = "dev"
}
variable "region" {
  type    = string
  default = "ca-central-1"
}
variable "image_tag" {
  type    = string
  default = null # null means "deploy the last green image"
}
variable "waf_rate_limit" {
  type    = number
  default = 300 # requests per 5 minutes per IP
}
