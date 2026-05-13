# scripts 說明

| 檔案 | 用途 |
|---|---|
| `00_create_project_structure.ps1` | 建立程式碼與測試資料夾骨架 |
| `10_install_agent_skills_repo_scoped.ps1` | clone addyosmani/agent-skills，複製選定 skills 到 `.agents\skills` |
| `20_start_codex.ps1` | 在專案根目錄啟動 Codex |
| `30_git_init.ps1` | 初始化 Git |

建議順序：

```powershell
.\scripts\00_create_project_structure.ps1
.\scripts\10_install_agent_skills_repo_scoped.ps1
.\scripts\30_git_init.ps1
.\scripts\20_start_codex.ps1
```
