# WorldHub MCP — 安装与注册

让 HermesAgent 以你的 neowow 身份读写 WorldHub 世界。

## 前置
- `pip install mcp`
- 在 Hermes WebUI 设置里填好 **neowow Token**（写入 `~/.hermes/webui/neowow.json`）。

## 注册（profile 的 config.yaml）
```yaml
mcp_servers:
  worldhub:
    command: /path/to/venv/bin/python3
    args: [/path/to/hermes-installer/webui/worldhub_mcp.py]
    # 可选覆盖（默认指向生产）：
    # env:
    #   WORLDHUB_APP_BASE: https://app.neowow.studio
    #   WORLDHUB_SUPABASE_URL: https://nazmftasoknlcwnlftow.supabase.co
```

## 安装技能
把 `webui/skills_seed/worldhub-worldbuilder/` 复制到 `~/.hermes/skills/worldhub-worldbuilder/`，或经 WebUI 技能面板导入。

## 用法
对 Hermes 说：「帮我编辑世界 <名称或 slug>」。它会先读现有设定、做一致性检查，再写回（贡献者→待审提案，创始人→直接生效）。

## 工具
`list_worlds` · `get_world` · `search_entities` · `check_consistency` · `submit_world_changes`
