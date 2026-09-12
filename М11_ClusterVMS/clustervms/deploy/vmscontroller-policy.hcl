# deploy/vmscontroller-policy.hcl — bound to job vmscontroller's workload identity.
# The only writer of vms/*; reads everything.
namespace "default" {
  variables {
    path "vms/*"        { capabilities = ["write", "read", "list", "destroy"] }
    path "objects/vms/snapshot" { capabilities = ["write", "read"] }
    path "objects/*"    { capabilities = ["read", "list"] }
    path "*"            { capabilities = ["read", "list"] }
  }
}
