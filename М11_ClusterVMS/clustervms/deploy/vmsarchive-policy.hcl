# deploy/vmsarchive-policy.hcl — bound to job vmsarchive's workload identity.
# A resource writes its own heartbeat and reads camera rows for retention and
# the mirror knob. It never writes vms/*. Mirrors go resource to resource over
# HTTP, not through any store.
namespace "default" {
  variables {
    path "objects/vms/resources/*" { capabilities = ["write", "read", "list"] }
    path "vms/*"                   { capabilities = ["read", "list"] }
    path "*/retention_days"        { capabilities = ["read"] }
  }
}
