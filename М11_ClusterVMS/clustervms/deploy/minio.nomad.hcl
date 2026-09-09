# deploy/minio.nomad.hcl — the cluster's object store: the restore point.
# One instance per server, host volume on /data/restore, so the object a
# Node published is on the same room's disks. Lesson 27 needs it reachable
# to FAIL OVER, never to run. Anonymous PUT/GET on the `cluster-restore`
# bucket is what HttpObjectStore expects; scope it to the cluster network.
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
        volumes      = ["/data/restore:/data:z"]
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
