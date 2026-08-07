package com.sakikomind.agent;

import com.sakikomind.llm.LlmGateway;

public class GeneralAgent extends BaseAgent {

    public GeneralAgent(LlmGateway llmGateway) {
        super(llmGateway);
    }

    @Override
    public AgentType type() {
        return AgentType.GENERAL;
    }

    @Override
    protected String systemPrompt() {
        return "你是 SakikoMind 智能客服。友好、简洁地回答用户问题。如果问题超出能力范围，说明原因并建议转接专业客服。";
    }
}
