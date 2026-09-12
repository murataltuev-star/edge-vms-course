# deploy/minio.nomad.hcl — the cluster's object store: heartbeats and the
# snapshot for the domain. One instance per server on /data/objects; the
# `vms` bucket, credentials in the Variable vms/objects (the jobs read it
# through a template). Nothing here is footage: the archive is a resource
# on each server's own disks and never enters this store.
job "minio" {
  datacenters = ["room-a"]
  type        = "system"

  group "minio" {
    network {
      mode = "host"
      port "s3" { static = 9000 }
    }
    task "minio" {
      driver = "podman"
      config {
        image        = "quay.io/minio/minio:latest"
        network_mode = "host"
        args         = ["server", "/data", "--address", ":9000"]
        volumes      = ["/data/objects:/data:z"]
      }
      template {
        data        = <<-EOT
          MINIO_ROOT_USER={{ with nomadVar "cluster/minio" }}{{ .user }}{{ end }}
          MINIO_ROOT_PASSWORD={{ with nomadVar "cluster/minio" }}{{ .password }}{{ end }}
        EOT
        destination = "secrets/minio.env"
        env         = true
      }
      resources { cpu = 300  memory = 512 }
    }
  }
}
