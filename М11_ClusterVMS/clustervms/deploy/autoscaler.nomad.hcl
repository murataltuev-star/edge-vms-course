# deploy/autoscaler.nomad.hcl — the Nomad Autoscaler (MPL-2.0), the fourth
# cluster-level job. It reads vms_worker_load from the console's /metrics
# through Prometheus and moves vmsworker's count inside the bounds the
# scaling block sets. Nobody in the VMS talks to it; it talks to Nomad.
job "autoscaler" {
  datacenters = ["room-a"]
  type        = "service"

  group "autoscaler" {
    count = 1
    task "autoscaler" {
      driver = "podman"
      identity { env = true }
      config {
        image        = "docker.io/hashicorp/nomad-autoscaler:latest"
        network_mode = "host"
        args         = ["agent", "-config", "/local/config.hcl"]
      }
      template {
        data        = <<EOT
nomad {
  address = "http://127.0.0.1:4646"
  token   = "{{ env "NOMAD_TOKEN" }}"
}
apm "prometheus" {
  driver = "prometheus"
  config = { address = "http://127.0.0.1:9090" }     # scrapes vms-console:8080/metrics
}
strategy "target-value" { driver = "target-value" }
EOT
        destination = "local/config.hcl"
      }
      resources { cpu = 100  memory = 128 }
    }
  }
}
