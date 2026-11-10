"""IVI application slots on the Cockpit board: the Cluster's A/B + external-verifier logic, other service.

The external verifier here is a long-running probation supervisor, so it may take far longer than the
Cluster's one-minute observation; its own deadlines (probation, safe-window wait) bound it.
"""
from .cluster import ClusterDevice


class IviDevice(ClusterDevice):
    service_name = "capstone-ivi.service"
    verifier_timeout = 3600
