package com.sakikomind.llm;

import com.sakikomind.config.SakikoMindProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.ai.chat.messages.Message;
import org.springframework.ai.chat.messages.SystemMessage;
import org.springframework.ai.chat.messages.UserMessage;
import org.springframework.ai.chat.model.ChatModel;
import org.springframework.ai.chat.model.ChatResponse;
import org.springframework.ai.chat.prompt.ChatOptions;
import org.springframework.ai.chat.prompt.Prompt;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;

@Component
public class SpringAiLlmGateway implements LlmGateway {

    private static final Logger log = LoggerFactory.getLogger(SpringAiLlmGateway.class);
    private static final String STARTUP_PLACEHOLDER_KEY = "sakikomind-local-placeholder";

    private final ChatModel chatModel;
    private final SakikoMindProperties properties;
    private final String activeProfile;
    private final boolean modelCredentialsConfigured;

    public SpringAiLlmGateway(ObjectProvider<ChatModel> chatModelProvider,
                              SakikoMindProperties properties,
                              @Value("${spring.profiles.active:anthropic}") String activeProfile,
                              @Value("${spring.ai.anthropic.api-key:}") String anthropicApiKey,
                              @Value("${spring.ai.deepseek.api-key:}") String deepSeekApiKey) {
        this.chatModel = chatModelProvider.getIfAvailable();
        this.properties = properties;
        this.activeProfile = activeProfile;
        this.modelCredentialsConfigured = credentialsConfigured(activeProfile, anthropicApiKey, deepSeekApiKey);
    }

    @Override
    public String chat(String systemPrompt, String userPrompt, double temperature, int maxTokens) {
        try {
            if (chatModel == null) {
                throw new IllegalStateException("Spring AI ChatModel is not configured");
            }
            if (!modelCredentialsConfigured) {
                throw new IllegalStateException("No real API key configured for active LLM profile: " + activeProfile);
            }
            List<Message> messages = new ArrayList<>();
            if (systemPrompt != null && !systemPrompt.isBlank()) {
                messages.add(new SystemMessage(clean(systemPrompt)));
            }
            messages.add(new UserMessage(clean(userPrompt)));
            ChatOptions options = ChatOptions.builder()
                    .temperature(temperature)
                    .maxTokens(maxTokens)
                    .build();
            ChatResponse response = chatModel.call(new Prompt(messages, options));
            if (response == null || response.getResult() == null || response.getResult().getOutput() == null) {
                return "";
            }
            return response.getResult().getOutput().getText();
        } catch (Exception ex) {
            if (!properties.getLlm().isFallbackEnabled()) {
                throw ex;
            }
            log.warn("Spring AI chat failed, using deterministic fallback: {}", ex.getMessage());
            return fallback(userPrompt);
        }
    }

    private String fallback(String userPrompt) {
        String prompt = clean(userPrompt);
        if (prompt.contains("退款") || prompt.toLowerCase().contains("refund")) {
            return "根据当前知识库，退款通常需要先提交申请并等待审核。请提供订单号，我可以继续帮你判断是否需要转人工审核。";
        }
        if (prompt.contains("报错") || prompt.contains("登录") || prompt.toLowerCase().contains("error")) {
            return "我建议先确认账号状态、网络环境和错误码。如果问题持续，请提供错误码和发生时间，技术支持会进一步排查。";
        }
        if (prompt.contains("扣款") || prompt.contains("账单") || prompt.contains("发票")) {
            return "账单问题需要核对支付记录、订单号和扣款时间。请提供相关信息，涉及退款或发票开具时会进入人工审核。";
        }
        return "我已收到你的问题。当前模型服务不可用，系统返回了本地降级回复；请稍后重试或转人工处理。";
    }

    private static String clean(String value) {
        return value == null ? "" : value;
    }

    private static boolean credentialsConfigured(String activeProfile, String anthropicApiKey, String deepSeekApiKey) {
        String profile = clean(activeProfile).toLowerCase();
        if (profile.contains("deepseek")) {
            return hasRealKey(deepSeekApiKey);
        }
        return hasRealKey(anthropicApiKey);
    }

    private static boolean hasRealKey(String value) {
        String key = clean(value).trim();
        return !key.isBlank() && !STARTUP_PLACEHOLDER_KEY.equals(key);
    }
}
