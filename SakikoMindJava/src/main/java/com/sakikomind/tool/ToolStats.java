package com.sakikomind.tool;

public class ToolStats {

    private long total;
    private long success;
    private long failed;
    private long totalLatencyMs;
    private int consecutiveFails;

    public synchronized void record(boolean ok, long latencyMs) {
        total++;
        totalLatencyMs += Math.max(0, latencyMs);
        if (ok) {
            success++;
            consecutiveFails = 0;
        } else {
            failed++;
            consecutiveFails++;
        }
    }

    public long total() {
        return total;
    }

    public double successRate() {
        return total == 0 ? 1.0 : (double) success / total;
    }

    public double avgLatencyMs() {
        return total == 0 ? 0.0 : (double) totalLatencyMs / total;
    }

    public int consecutiveFails() {
        return consecutiveFails;
    }
}
