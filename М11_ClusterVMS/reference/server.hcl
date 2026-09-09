# reference/server.hcl — Lesson 1: one of three Nomad servers. Raft needs a
# quorum: three tolerates one loss, five tolerates two. Never two, never four.
datacenter = "room-a"
data_dir   = "/data/nomad"

server {
  enabled          = true
  bootstrap_expect = 3
  server_join {
    retry_join = ["10.0.0.11", "10.0.0.12", "10.0.0.13"]
  }
}

acl {
  enabled = true                        # Lesson 2: Variables are ACL'd; one writer per key
}
