package com.sakikomind.agent;

import com.sakikomind.llm.LlmGateway;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.util.List;
import java.util.Map;

@Configuration
public class AgentConfig {

    @Bean
    Map<AgentType, List<BaseAgent>> agentPool(LlmGateway llmGateway) {
        return Map.of(
                AgentType.GENERAL, List.of(new GeneralAgent(llmGateway)),
                AgentType.TECHNICAL, List.of(new TechnicalAgent(llmGateway)),
                AgentType.BILLING, List.of(new BillingAgent(llmGateway))
        );
    }
}
