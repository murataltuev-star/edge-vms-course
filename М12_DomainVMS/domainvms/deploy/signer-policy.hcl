# The signer is the only writer of its keys, its users, and what it publishes to agents.
namespace "default" {
  variables {
    path "domain/signer"    { capabilities = ["read", "write"] }
    path "identity/*"       { capabilities = ["read", "write"] }
    path "domain/keys"      { capabilities = ["read", "write"] }
    path "domain/revoked"   { capabilities = ["read", "write"] }
    path "domain/licence"   { capabilities = ["read", "write"] }
    path "domain/placement" { capabilities = ["read", "write"] }
    path "domain/placement/*" { capabilities = ["read", "write"] }
  }
}
