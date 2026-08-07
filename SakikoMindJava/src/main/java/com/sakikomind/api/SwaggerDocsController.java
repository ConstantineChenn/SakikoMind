package com.sakikomind.api;

import org.springframework.http.MediaType;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.ResponseBody;

@Controller
public class SwaggerDocsController {

    @GetMapping(value = "/docs", produces = MediaType.TEXT_HTML_VALUE)
    @ResponseBody
    public String docs() {
        return """
                <!doctype html>
                <html lang="zh-CN">
                <head>
                  <meta charset="utf-8">
                  <meta name="viewport" content="width=device-width, initial-scale=1">
                  <title>SakikoMind Java API Docs</title>
                  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
                  <style>
                    body { margin: 0; background: #f7f7f7; }
                    .topbar { display: none; }
                  </style>
                </head>
                <body>
                  <div id="swagger-ui"></div>
                  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
                  <script>
                    window.onload = function () {
                      window.ui = SwaggerUIBundle({
                        url: "/v3/api-docs",
                        dom_id: "#swagger-ui",
                        deepLinking: true,
                        displayOperationId: false,
                        defaultModelsExpandDepth: 1,
                        defaultModelExpandDepth: 1,
                        docExpansion: "list",
                        filter: true,
                        tryItOutEnabled: true
                      });
                    };
                  </script>
                </body>
                </html>
                """;
    }
}
