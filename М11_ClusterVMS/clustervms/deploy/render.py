#!/usr/bin/env python3
"""Render a Node's jobspec, agent-side Variables and ACL policy.

    python3 deploy/render.py node-3 --vlan cctv-a --datacenter room-a --cpu 2000 --memory 2048 > node-3.nomad.hcl
    python3 deploy/render.py node-3 --policy > node-3-policy.hcl
    python3 deploy/render.py node-3 --bootstrap          # prints the `nomad var put` lines for a new Node

One job per Node; the job name IS the Node's identity. Memory and CPU come
from М10's shard-memory-probe: B + n × I, rounded up.
"""
from __future__ import annotations

import argparse
import secrets

JOB = """job "{node}" {{
  datacenters = ["{dc}"]
  type        = "service"

  group "node" {{
    count = 1

    constraint {{
      attribute = "${{meta.vlans}}"
      operator  = "set_contains"
      value     = "{vlan}"
    }}

    disconnect {{
      lost_after           = "{lost_after}"
      replace              = true
      stop_on_client_after = "{stop_after}"
      reconcile            = "keep_replacement"
    }}

    reschedule {{
      delay          = "15s"
      delay_function = "exponential"
      max_delay      = "2m"
      unlimited      = true
    }}

    restart {{
      attempts = 3
      interval = "10m"
      delay    = "15s"
      mode     = "delay"
    }}

    network {{
      mode = "host"
      port "console" {{ static = {port} }}
    }}

    task "postgres" {{
      driver = "podman"
      config {{
        image        = "docker.io/library/postgres:16"
        network_mode = "host"
        volumes      = ["/data/nodes/{node}/pg:/var/lib/postgresql/data:z"]
      }}
      template {{
        data        = <<-EOT
          POSTGRES_USER=nodevms
          POSTGRES_DB=nodevms
          POSTGRES_PASSWORD={{{{ with nomadVar "nodes/{node}/pg" }}}}{{{{ .password }}}}{{{{ end }}}}
        EOT
        destination = "secrets/pg.env"
        env         = true
      }}
      resources {{ cpu = 500  memory = 512 }}
    }}

    task "apphost" {{
      driver = "podman"
      config {{
        image        = "localhost/clustervms-apphost:latest"
        network_mode = "host"
        volumes = [
          "/data/nodes/{node}/archive:/data/archive:z",
          "/data/nodes/{node}/config:/data/config:z",
        ]
      }}
      identity {{
        env  = true
        file = true
      }}
      template {{
        data        = <<-EOT
          {{{{ with nomadVar "nodes/{node}" }}}}
          NODE_ID={{{{ .node }}}}
          CONFIG_OBJECT={{{{ .config }}}}
          CONFIG_REVISION={{{{ .revision }}}}
          CAMERA_IDS={{{{ .cameras }}}}
          {{{{ end }}}}
          DATABASE_URL=postgresql://nodevms:{{{{ with nomadVar "nodes/{node}/pg" }}}}{{{{ .password }}}}{{{{ end }}}}@127.0.0.1:5432/nodevms
          OBJECT_STORE_URL={object_store}
          ARCHIVE_DIR=/data/archive
          COLUMN_KEY_FILE=/data/config/column.key
          CONSOLE_PORT={port}
          LEASE_TTL={lease_ttl}
          LEASE_MARGIN={lease_margin}
          PUBLISH_FLOOR=5
          HEARTBEAT_INTERVAL=30
        EOT
        destination = "local/node.env"
        env         = true
        change_mode = "restart"
      }}
      resources {{ cpu = {cpu}  memory = {memory} }}
    }}
  }}
}}
"""

POLICY = """namespace "default" {{
  variables {{
    path "nodes/{node}"   {{ capabilities = ["read", "write"] }}
    path "nodes/{node}/*" {{ capabilities = ["read", "write"] }}
    path "nodes/*"        {{ capabilities = ["read"] }}
    path "placement/*"    {{ capabilities = ["read"] }}
  }}
}}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("node")
    ap.add_argument("--vlan", default="cctv-a")
    ap.add_argument("--datacenter", default="room-a")
    ap.add_argument("--cpu", type=int, default=2000)
    ap.add_argument("--memory", type=int, default=2048)
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--object-store", default="http://127.0.0.1:9000/cluster-restore")
    ap.add_argument("--lease-ttl", type=int, default=30)
    ap.add_argument("--lease-margin", type=int, default=5)
    ap.add_argument("--lost-after", default="2m")
    ap.add_argument("--stop-after", default="2m")
    ap.add_argument("--policy", action="store_true")
    ap.add_argument("--bootstrap", action="store_true")
    a = ap.parse_args()
    if a.policy:
        print(POLICY.format(node=a.node)); return
    if a.bootstrap:
        print(f'nomad var put nodes/{a.node} node={a.node} config="" revision=0 cameras=""')
        print(f'nomad var put nodes/{a.node}/pg password={secrets.token_hex(16)}')
        print(f'nomad var put nodes/{a.node}/key hex={secrets.token_hex(32)}')
        print(f'nomad acl policy apply -namespace default -job {a.node} {a.node} {a.node}-policy.hcl')
        return
    print(JOB.format(node=a.node, dc=a.datacenter, vlan=a.vlan, cpu=a.cpu, memory=a.memory, port=a.port,
                     object_store=a.object_store, lease_ttl=a.lease_ttl, lease_margin=a.lease_margin,
                     lost_after=a.lost_after, stop_after=a.stop_after))


if __name__ == "__main__":
    main()
