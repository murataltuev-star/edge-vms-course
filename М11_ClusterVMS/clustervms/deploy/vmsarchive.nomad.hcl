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
        OBJECTS      = "s3+http://127.0.0.1:9000/vms?region=us-east-1"
        RESOURCE_URL = "http://${attr.unique.network.ip-address}:8090"
      }
      template {
        data        = <<EOT
{{ with nomadVar "vms/objects" }}AWS_ACCESS_KEY_ID={{ .access_key }}
AWS_SECRET_ACCESS_KEY={{ .secret_key }}{{ end }}
EOT
        destination = "secrets/objects.env"
        env         = true
      }
      resources { cpu = 200  memory = 256 }
    }
  }
}
