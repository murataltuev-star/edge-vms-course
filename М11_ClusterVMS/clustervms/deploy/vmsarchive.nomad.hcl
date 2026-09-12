# deploy/vmsarchive.nomad.hcl — the archive resource: a system job, one
# allocation on every server that declares meta.archive, pinned there for
# as long as the server exists. It has no controller: a heartbeat so the
# console knows it exists, its manifests served so a timeline can span
# servers, and a policy (repair, then retention) every ten minutes.
job "vmsarchive" {
  datacenters = ["room-a"]
  type        = "system"

  constraint {
    attribute = "${meta.archive}"
    operator  = "is_set"
  }

  group "vmsarchive" {
    network {
      mode = "host"
      port "manifests" { static = 8090 }
    }
    task "vmsarchive" {
      driver = "podman"
      identity { env = true }
      config {
        image        = "localhost/clustervms:latest"
        network_mode = "host"
        args         = ["python3", "-m", "cluster", "resource"]
        volumes      = ["/data/spool:/data/spool", "/data/archive:/data/archive"]
      }
      env {
        OBJECTS      = "variables://objects"       # its heartbeat as a Variable; no MinIO on this cluster
        RESOURCE_URL = "http://${attr.unique.network.ip-address}:8090"   # where peers PUT mirrors and the index reads buckets
      }
      service {                                    # peers find each other here; verify-bench uses it
        name = "vmsarchive"
        port = "manifests"
      }
      resources { cpu = 200  memory = 256 }
    }
  }
}
