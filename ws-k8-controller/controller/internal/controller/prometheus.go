package controller

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
)

func queryTotalConnections() (int, error) {

	resp, err := http.Get("http://prometheus.monitoring.svc.cluster.local:9090/api/v1/query?query=sum(active_connections)")
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()

	var result struct {
		Data struct {
			Result []struct {
				Value []interface{} `json:"value"`
			} `json:"result"`
		} `json:"data"`
	}

	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return 0, err
	}

	if len(result.Data.Result) == 0 {
		return 0, nil
	}

	valueStr := result.Data.Result[0].Value[1].(string)
	valueFloat, _ := strconv.ParseFloat(valueStr, 64)

	return int(valueFloat), nil
}

// queryPerPodConnections queries Prometheus for per-pod active_connections.
// Returns a map of pod_name -> connection_count.
func queryPerPodConnections() (map[string]int, error) {
	resp, err := http.Get("http://prometheus.monitoring.svc.cluster.local:9090/api/v1/query?query=active_connections")
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var result struct {
		Data struct {
			Result []struct {
				Metric map[string]string `json:"metric"`
				Value  []interface{}     `json:"value"`
			} `json:"result"`
		} `json:"data"`
	}

	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, err
	}

	podConns := make(map[string]int)
	for _, r := range result.Data.Result {
		pod := r.Metric["pod"]
		if pod == "" {
			pod = r.Metric["kubernetes_pod_name"]
		}
		if pod == "" {
			pod = r.Metric["instance"]
		}
		if pod == "" {
			continue
		}
		if len(r.Value) < 2 {
			continue
		}
		valStr, ok := r.Value[1].(string)
		if !ok {
			continue
		}
		valFloat, _ := strconv.ParseFloat(valStr, 64)
		podConns[pod] = int(valFloat)
	}

	return podConns, nil
}

// DrainStatus represents the response from a broker's /drain/status endpoint.
type DrainStatus struct {
	Draining   bool `json:"draining"`
	Remaining  int  `json:"remaining"`
	TotalStart int  `json:"total_at_start"`
}

// queryDrainStatus queries a specific pod's drain status.
func queryDrainStatus(podIP string) (*DrainStatus, error) {
	resp, err := http.Get(fmt.Sprintf("http://%s:8080/drain/status", podIP))
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var status DrainStatus
	if err := json.NewDecoder(resp.Body).Decode(&status); err != nil {
		return nil, err
	}
	return &status, nil
}

// triggerDrain calls POST /drain on a specific pod.
func triggerDrain(podIP string) error {
	resp, err := http.Post(fmt.Sprintf("http://%s:8080/drain", podIP), "text/plain", nil)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	return nil
}
