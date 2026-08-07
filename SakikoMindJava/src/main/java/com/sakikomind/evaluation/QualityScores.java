package com.sakikomind.evaluation;

public record QualityScores(
        double relevance,
        double accuracy,
        double completeness,
        double helpfulness,
        boolean judgeFailed,
        String error
) {
    public double overall() {
        return (relevance + accuracy + completeness + helpfulness) / 4.0;
    }
}
