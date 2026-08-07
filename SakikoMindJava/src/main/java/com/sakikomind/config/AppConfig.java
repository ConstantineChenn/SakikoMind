package com.sakikomind.config;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableConfigurationProperties(SakikoMindProperties.class)
public class AppConfig {
}
