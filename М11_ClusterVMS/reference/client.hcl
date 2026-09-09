# reference/client.hcl — Lesson 25: a Nomad client on an appliance server.
# Servers get server.hcl; on the bench the same three boxes run both.
datacenter = "room-a"
data_dir   = "/data/nomad"
plugin_dir = "/data/nomad/plugins"      # nomad-driver-podman and exec2 live here: neither is built in

client {
  enabled = true
  servers = ["10.0.0.11:4647", "10.0.0.12:4647", "10.0.0.13:4647"]
  meta {
    vlans = "cctv-a,cctv-b"             # what this server's NICs can reach; Lesson 26's constraint
  }
  host_volume "nodes" {
    path = "/data/nodes"
  }
}

plugin "nomad-driver-podman" {
  config {
    socket_path = "unix:///run/podman/podman.sock"   # systemctl enable --now podman.socket
  }
}
