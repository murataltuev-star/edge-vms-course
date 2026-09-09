"""ClusterVMS — М11. What a Node needs to outlive its server.

Built on М10's nodevms (imported, not copied): identity from a Nomad
Variable, the epoch by check-and-set, configuration published upward
object-first, the restore, a lease that fences the zombie, the cluster
directory, and placement by measured capacity.
"""
import os
import sys

# nodevms is the М10 project, two directories up. In the container image it
# is copied beside this package; on a checkout it is found by path.
_here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for cand in (os.environ.get("NODEVMS_PATH", ""),
             os.path.join(_here, "nodevms"),
             os.path.join(os.path.dirname(os.path.dirname(_here)), "М10_NodeVMS", "nodevms")):
    if cand and os.path.isdir(cand) and cand not in sys.path:
        sys.path.append(cand)          # append, not insert: our own tests/ must win
        break
