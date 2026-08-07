package com.sakikomind.config;

import io.swagger.v3.oas.models.OpenAPI;
import io.swagger.v3.oas.models.info.Info;
import io.swagger.v3.oas.models.info.License;
import io.swagger.v3.oas.models.servers.Server;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.util.List;

@Configuration
public class OpenApiConfig {

    @Bean
    public OpenAPI sakikoMindOpenAPI() {
        return new OpenAPI()
                .info(new Info()
                        .title("SakikoMind Java API")
                        .version("0.1.0")
                        .description("SakikoMind Java 智能客服系统接口文档，支持在线调试 /chat、/search、知识库、监控和评测接口。")
                        .license(new License().name("Internal Project")))
                .servers(List.of(
                        new Server().url("http://localhost:8080").description("Local Java App"),
                        new Server().url("http://localhost:8081").description("Local Nginx Proxy")
                ));
    }
}
