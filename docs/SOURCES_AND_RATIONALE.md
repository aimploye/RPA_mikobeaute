# 參考來源與設定理由

本文件包根據以下原則整理：

1. Codex CLI 是本機 coding agent，會在選定目錄讀寫與執行程式。
2. `AGENTS.md` 適合放 repo 規則與驗收條件。
3. `.codex/config.toml` 適合放 project-scoped 設定，但需在 trusted project 才載入。
4. Skills 應選擇性載入，不要一口氣塞滿 context。
5. 本專案應先做可在沒有 POS 實機驗證的低風險部分。

參考：

- OpenAI Codex CLI docs: https://developers.openai.com/codex/cli
- OpenAI AGENTS.md docs: https://developers.openai.com/codex/guides/agents-md
- OpenAI Codex config docs: https://developers.openai.com/codex/config-basic
- OpenAI Codex config reference: https://developers.openai.com/codex/config-reference
- OpenAI Codex skills docs: https://developers.openai.com/codex/skills
- addyosmani/agent-skills: https://github.com/addyosmani/agent-skills
