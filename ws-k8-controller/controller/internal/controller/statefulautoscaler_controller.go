package controller

import (
	"context"
	"fmt"
	"math"
	"sync"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"

	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	ctrllog "sigs.k8s.io/controller-runtime/pkg/log"

	autoscalingv1alpha1 "star/controller/api/v1alpha1"
)

type replicaHistory struct {
	Timestamp time.Time
	Replicas  int32
}

var (
	historyMu        sync.Mutex
	scaleDownHistory = make(map[types.NamespacedName][]replicaHistory)
)

func getStabilizedDesiredReplicas(nn types.NamespacedName, newDesired int32, window time.Duration) int32 {
	if window <= 0 {
		return newDesired
	}

	historyMu.Lock()
	defer historyMu.Unlock()

	now := time.Now()
	history := scaleDownHistory[nn]

	var validHistory []replicaHistory
	for _, h := range history {
		if now.Sub(h.Timestamp) <= window {
			validHistory = append(validHistory, h)
		}
	}
	validHistory = append(validHistory, replicaHistory{Timestamp: now, Replicas: newDesired})
	scaleDownHistory[nn] = validHistory

	stabilized := newDesired
	for _, h := range validHistory {
		if h.Replicas > stabilized {
			stabilized = h.Replicas
		}
	}

	return stabilized
}

// StatefulAutoscalerReconciler reconciles a StatefulAutoscaler object
type StatefulAutoscalerReconciler struct {
	client.Client
	Scheme *runtime.Scheme
}

// RBAC for CRD
// +kubebuilder:rbac:groups=autoscaling.star.local,resources=statefulautoscalers,verbs=get;list;watch;update;patch
// +kubebuilder:rbac:groups=autoscaling.star.local,resources=statefulautoscalers/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=autoscaling.star.local,resources=statefulautoscalers/finalizers,verbs=update

// RBAC for Deployments
// +kubebuilder:rbac:groups=apps,resources=deployments,verbs=get;list;watch;update;patch

// RBAC for Pods (needed for per-pod drain orchestration)
// +kubebuilder:rbac:groups="",resources=pods,verbs=get;list;watch

func (r *StatefulAutoscalerReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	log := ctrllog.FromContext(ctx)

	var autoscaler autoscalingv1alpha1.StatefulAutoscaler
	if err := r.Get(ctx, req.NamespacedName, &autoscaler); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}

	if autoscaler.Spec.TargetRef.Name == "" {
		return ctrl.Result{}, nil
	}

	var deployment appsv1.Deployment
	if err := r.Get(ctx,
		types.NamespacedName{
			Name:      autoscaler.Spec.TargetRef.Name,
			Namespace: req.Namespace,
		},
		&deployment); err != nil {
		return ctrl.Result{}, err
	}

	currentReplicas := int32(0)
	if deployment.Spec.Replicas != nil {
		currentReplicas = *deployment.Spec.Replicas
	}

	totalConnections, err := queryTotalConnections()
	if err != nil {
		log.Error(err, "Failed to query total connections from Prometheus")
		return ctrl.Result{RequeueAfter: 10 * time.Second}, nil
	}

	if autoscaler.Spec.TargetConnectionsPerPod == 0 {
		return ctrl.Result{RequeueAfter: 10 * time.Second}, nil
	}

	rawDesired := int32(math.Ceil(
		float64(totalConnections) /
			float64(autoscaler.Spec.TargetConnectionsPerPod),
	))

	if autoscaler.Spec.MinReplicas != nil && rawDesired < *autoscaler.Spec.MinReplicas {
		rawDesired = *autoscaler.Spec.MinReplicas
	}

	if autoscaler.Spec.MaxReplicas != nil && rawDesired > *autoscaler.Spec.MaxReplicas {
		rawDesired = *autoscaler.Spec.MaxReplicas
	}

	window := time.Duration(autoscaler.Spec.ScaleDownCooldownSeconds) * time.Second
	desired := getStabilizedDesiredReplicas(req.NamespacedName, rawDesired, window)

	log.Info("Reconcile loop",
		"totalConnections", totalConnections,
		"currentReplicas", currentReplicas,
		"rawDesired", rawDesired,
		"stabilizedDesired", desired,
		"drainInProgress", autoscaler.Status.DrainInProgress,
	)

	// ── Scale UP path ──────────────────────────────────────────────
	if desired > currentReplicas {
		step := autoscaler.Spec.MaxScaleUpStep
		if step > 0 && desired-currentReplicas > step {
			desired = currentReplicas + step
		}

		log.Info("Scaling UP",
			"from", currentReplicas,
			"to", desired,
			"reason", fmt.Sprintf("connections=%d exceeds capacity=%d",
				totalConnections, int(currentReplicas)*int(autoscaler.Spec.TargetConnectionsPerPod)),
		)

		deployment.Spec.Replicas = &desired
		if err := r.Update(ctx, &deployment); err != nil {
			return ctrl.Result{}, err
		}

		// Update status
		autoscaler.Status.CurrentReplicas = desired
		autoscaler.Status.DesiredReplicas = desired
		if err := r.Status().Update(ctx, &autoscaler); err != nil {
			log.Error(err, "Failed to update status after scale-up")
		}

		return ctrl.Result{RequeueAfter: 5 * time.Second}, nil
	}

	// ── Scale DOWN path (with drain orchestration) ─────────────────
	if desired < currentReplicas {
		// If drain is not enabled, do simple scale-down (original behavior)
		if !autoscaler.Spec.Drain.Enabled {
			step := autoscaler.Spec.MaxScaleDownStep
			if step > 0 && currentReplicas-desired > step {
				desired = currentReplicas - step
			}

			log.Info("Scaling DOWN (drain disabled)",
				"from", currentReplicas,
				"to", desired,
			)

			deployment.Spec.Replicas = &desired
			if err := r.Update(ctx, &deployment); err != nil {
				return ctrl.Result{}, err
			}
			return ctrl.Result{RequeueAfter: 5 * time.Second}, nil
		}

		// ── Drain-aware scale-down ──────────────────────────────────
		// Check if a drain is already in progress
		if autoscaler.Status.DrainInProgress {
			// Poll drain status
			drainPodIP := autoscaler.Status.DrainingPodIP
			drainPodName := autoscaler.Status.DrainingPod

			if drainPodIP == "" {
				// Lost drain state — reset and retry
				log.Info("Drain state lost, resetting")
				autoscaler.Status.DrainInProgress = false
				autoscaler.Status.DrainingPod = ""
				autoscaler.Status.DrainingPodIP = ""
				autoscaler.Status.DrainStartTime = nil
				if err := r.Status().Update(ctx, &autoscaler); err != nil {
					log.Error(err, "Failed to reset drain status")
				}
				return ctrl.Result{RequeueAfter: 5 * time.Second}, nil
			}

			// Check timeout
			drainTimeout := time.Duration(autoscaler.Spec.Drain.TimeoutSeconds) * time.Second
			if autoscaler.Status.DrainStartTime != nil {
				elapsed := time.Since(autoscaler.Status.DrainStartTime.Time)
				if elapsed > drainTimeout {
					log.Info("Drain timeout reached, proceeding with scale-down",
						"pod", drainPodName,
						"elapsed", elapsed.String(),
						"timeout", drainTimeout.String(),
					)
					// Proceed to scale down regardless
					scaleTarget := currentReplicas - 1
					deployment.Spec.Replicas = &scaleTarget
					if err := r.Update(ctx, &deployment); err != nil {
						return ctrl.Result{}, err
					}

					autoscaler.Status.DrainInProgress = false
					autoscaler.Status.DrainingPod = ""
					autoscaler.Status.DrainingPodIP = ""
					autoscaler.Status.DrainStartTime = nil
					autoscaler.Status.CurrentReplicas = scaleTarget
					if err := r.Status().Update(ctx, &autoscaler); err != nil {
						log.Error(err, "Failed to update status after timeout scale-down")
					}
					return ctrl.Result{RequeueAfter: 5 * time.Second}, nil
				}
			}

			// Poll drain status from broker
			status, err := queryDrainStatus(drainPodIP)
			if err != nil {
				log.Info("Could not reach draining pod, assuming drained",
					"pod", drainPodName, "error", err.Error())
				// Pod may already be gone — proceed with scale-down
				scaleTarget := currentReplicas - 1
				deployment.Spec.Replicas = &scaleTarget
				if err := r.Update(ctx, &deployment); err != nil {
					return ctrl.Result{}, err
				}

				autoscaler.Status.DrainInProgress = false
				autoscaler.Status.DrainingPod = ""
				autoscaler.Status.DrainingPodIP = ""
				autoscaler.Status.DrainStartTime = nil
				autoscaler.Status.CurrentReplicas = scaleTarget
				if err := r.Status().Update(ctx, &autoscaler); err != nil {
					log.Error(err, "Failed to update status")
				}
				return ctrl.Result{RequeueAfter: 5 * time.Second}, nil
			}

			log.Info("Drain progress",
				"pod", drainPodName,
				"remaining", status.Remaining,
				"draining", status.Draining,
			)

			if status.Remaining == 0 {
				// Drain complete — now scale down by 1
				log.Info("Drain complete, scaling down",
					"pod", drainPodName,
					"from", currentReplicas,
					"to", currentReplicas-1,
				)

				scaleTarget := currentReplicas - 1
				deployment.Spec.Replicas = &scaleTarget
				if err := r.Update(ctx, &deployment); err != nil {
					return ctrl.Result{}, err
				}

				autoscaler.Status.DrainInProgress = false
				autoscaler.Status.DrainingPod = ""
				autoscaler.Status.DrainingPodIP = ""
				autoscaler.Status.DrainStartTime = nil
				autoscaler.Status.CurrentReplicas = scaleTarget
				if err := r.Status().Update(ctx, &autoscaler); err != nil {
					log.Error(err, "Failed to update status after drain scale-down")
				}
				return ctrl.Result{RequeueAfter: 5 * time.Second}, nil
			}

			// Drain still in progress — check again soon
			return ctrl.Result{RequeueAfter: 3 * time.Second}, nil
		}

		// ── Start a new drain ───────────────────────────────────────
		// Find the pod with fewest connections (least disruption to drain)
		perPodConns, err := queryPerPodConnections()
		if err != nil {
			log.Error(err, "Failed to query per-pod connections")
			return ctrl.Result{RequeueAfter: 10 * time.Second}, nil
		}

		// List deployment pods to get IPs
		var podList corev1.PodList
		if err := r.List(ctx, &podList,
			client.InNamespace(req.Namespace),
			client.MatchingLabels(deployment.Spec.Selector.MatchLabels),
		); err != nil {
			log.Error(err, "Failed to list pods")
			return ctrl.Result{RequeueAfter: 10 * time.Second}, nil
		}

		// Find best drain candidate: running pod with fewest connections
		var victimPod *corev1.Pod
		victimConns := int(^uint(0) >> 1) // max int
		for i := range podList.Items {
			p := &podList.Items[i]
			if p.Status.Phase != corev1.PodRunning {
				continue
			}
			conns, found := perPodConns[p.Name]
			if !found {
				conns = 0
			}
			if conns < victimConns {
				victimConns = conns
				victimPod = p
			}
		}

		if victimPod == nil {
			log.Info("No running pods found for drain, scaling down directly")
			scaleTarget := currentReplicas - 1
			deployment.Spec.Replicas = &scaleTarget
			if err := r.Update(ctx, &deployment); err != nil {
				return ctrl.Result{}, err
			}
			return ctrl.Result{RequeueAfter: 5 * time.Second}, nil
		}

		podIP := victimPod.Status.PodIP
		log.Info("Starting drain on pod",
			"pod", victimPod.Name,
			"ip", podIP,
			"connections", victimConns,
			"reason", fmt.Sprintf("scale-down desired: %d -> %d", currentReplicas, desired),
		)

		// Trigger drain on the victim pod
		if err := triggerDrain(podIP); err != nil {
			log.Error(err, "Failed to trigger drain", "pod", victimPod.Name)
			// Still mark as draining so we retry
		}

		// Record drain state in CR status
		now := metav1.Now()
		autoscaler.Status.DrainInProgress = true
		autoscaler.Status.DrainingPod = victimPod.Name
		autoscaler.Status.DrainingPodIP = podIP
		autoscaler.Status.DrainStartTime = &now
		autoscaler.Status.DesiredReplicas = desired
		if err := r.Status().Update(ctx, &autoscaler); err != nil {
			log.Error(err, "Failed to update drain status")
		}

		return ctrl.Result{RequeueAfter: 3 * time.Second}, nil
	}

	// ── No scaling needed ──────────────────────────────────────────
	// Update status with current state
	autoscaler.Status.CurrentReplicas = currentReplicas
	autoscaler.Status.DesiredReplicas = desired
	if err := r.Status().Update(ctx, &autoscaler); err != nil {
		log.Error(err, "Failed to update status")
	}

	return ctrl.Result{RequeueAfter: 5 * time.Second}, nil
}

func (r *StatefulAutoscalerReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&autoscalingv1alpha1.StatefulAutoscaler{}).
		Complete(r)
}
