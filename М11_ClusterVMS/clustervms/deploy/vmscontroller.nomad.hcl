# deploy/vmscontroller.nomad.hcl — the controller: one, and safe at two.
# count = 1 is a preference; correctness is CAS. It has no Nomad client,
# so it needs no more of the API than any task gets: Variables under vms/*.
job "vmscontroller" {
  datacenters = ["room-a"]
  type        = "service"

  group "vmscontroller" {
    count = 1
    constraint {                                     # the console writes operator marks into THIS server's resource
      attribute = "${meta.archive}"
      operator  = "is_set"
    }
    network {
      mode = "host"
      port "console" { static = 8080 }
    }
    task "vmscontroller" {
      driver = "podman"
      identity { env = true }
      config {
        image        = "localhost/clustervms:latest"
        network_mode = "host"
        args         = ["python3", "-m", "cluster", "controller"]
        volumes      = ["/data/archive:/data/archive"]
      }
      env {
        OBJECTS      = "s3+http://127.0.0.1:9000/vms?region=us-east-1"
        ARCHIVE      = "/data/archive"
        CONSOLE_PORT = "8080"
        CLUSTER      = "room-a"
      }
      template {
        data        = <<EOT
{{ with nomadVar "vms/objects" }}AWS_ACCESS_KEY_ID={{ .access_key }}
AWS_SECRET_ACCESS_KEY={{ .secret_key }}{{ end }}
EOT
        destination = "secrets/objects.env"
        env         = true
      }
      service {                                      # what the autoscaler and М12's read model scrape
        name = "vms-console"
        port = "console"
        tags = ["metrics"]
      }
      resources { cpu = 300  memory = 256 }
    }
  }
}
