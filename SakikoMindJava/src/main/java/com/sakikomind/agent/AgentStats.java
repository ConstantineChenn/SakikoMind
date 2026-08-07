package com.sakikomind.agent;

import java.util.concurrent.atomic.AtomicLong;

public class AgentStats {

    private final AtomicLong total = new AtomicLong();
    private final AtomicLong success = new AtomicLong();
    private final AtomicLong totalLatencyMs = new AtomicLong();
    private volatile double monitorPenalty;

    public void record(boolean ok, long latencyMs) {
        total.incrementAndGet();
        if (ok) {
            success.incrementAndGet();
        }
        totalLatencyMs.addAndGet(Math.max(0, latencyMs));
    }

    public long total() {
        return total.get();
    }

    public double successRate() {
        long t = total.get();
        return t == 0 ? 1.0 : (double) success.get() / t;
    }

    public double avgLatencyMs() {
        long t = total.get();
        return t == 0 ? 0.0 : (double) totalLatencyMs.get() / t;
    }

    public double routingScore() {
        double latencyScore = 1.0 / (1.0 + avgLatencyMs() / 1000.0);
        double base = successRate() * 0.7 + latencyScore * 0.3;
        return base * Math.max(0.0, 1.0 - monitorPenalty);
    }

    public double monitorPenalty() {
        return monitorPenalty;
    }

    public void setMonitorPenalty(double monitorPenalty) {
        this.monitorPenalty = Math.min(0.9, Math.max(0.0, monitorPenalty));
    }
}
