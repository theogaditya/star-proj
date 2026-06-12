# Experiment-B Configuration Snapshot

Cluster:
- kind multi-node

Load:
- High clients: 800
- High duration: 60 seconds
- Low duration: 30 seconds
- Cycles: 5

Autoscaler:
- Min replicas: 2
- Max replicas: 10
- CPU target: 60%
- Scale-down stabilization: 300 seconds

Sampling interval:
- 5 seconds