# The same one-writer-per-prefix pattern verify-bench.sh checks for Nodes:
# a Node may write nodes/<node>/*; the agent may write domain/*; nobody else.
namespace "default" {
  variables {
    path "domain/keys"    { capabilities = ["read", "write"] }
    path "domain/revoked" { capabilities = ["read", "write"] }
    path "domain/*"       { capabilities = ["read"] }
    path "nodes/*"        { capabilities = ["deny"] }
  }
}
