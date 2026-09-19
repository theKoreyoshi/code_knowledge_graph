# VS Code AI Coding 插件接入 code-kg MCP 服务

本文以 **VS Code + GitHub Copilot Chat 的 Agent 模式**为例，将本项目提供的
`code-kg` MCP 服务接入 VS Code，让 AI 在修改 C/C++ 代码前先查询知识图谱，
再读取有限的源码上下文。

整体流程：

```text
C/C++ 工程
    -> ckg build
    -> entity.json / relation.json / run_meta.json
    -> VS Code MCP stdio 服务
    -> GitHub Copilot Agent
    -> search_code_symbols
    -> get_code_context
    -> 修改代码
    -> analyze_change_impact
    -> 编译和测试
```

## 1. 环境准备

### 1.1 安装 VS Code 和 AI 插件

安装最新稳定版 VS Code，并安装并登录 GitHub Copilot Chat。

在 VS Code 中确认可以打开 Chat，并且模型选择器中存在：

```text
Agent
```

MCP 工具需要在 Agent 会话中使用。VS Code 官方说明中，Agent 可以调用内置工具、
MCP 工具和扩展工具。

参考：

- [VS Code MCP Servers](https://code.visualstudio.com/docs/agent-customization/mcp-servers)
- [VS Code MCP Configuration Reference](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)
- [VS Code Use Tools with Agents](https://code.visualstudio.com/docs/agents/run/tools)

### 1.2 准备 Python 环境

打开 PowerShell，进入 `code-kg-tool` 项目目录：

```powershell
cd "C:\Users\29234\Documents\Codex\2026-09-16\c-users-29234-desktop-foc-codex\outputs\code-kg-tool"
```

建议创建独立虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

确认模块可以导入：

```powershell
python -c "import ckg; print(ckg.__file__)"
```

如果 PowerShell 禁止激活脚本，可以不激活虚拟环境，直接使用解释器绝对路径：

```powershell
.\.venv\Scripts\python.exe -c "import ckg; print(ckg.__file__)"
```

## 2. 构建代码知识图谱

以一个 C/C++ 工程为例：

```powershell
python -m ckg build `
  "D:\work\my-driver" `
  -o "D:\work\my-driver\ckg-out"
```

如果使用虚拟环境：

```powershell
.\.venv\Scripts\python.exe -m ckg build `
  "D:\work\my-driver" `
  -o "D:\work\my-driver\ckg-out"
```

构建成功后，确认以下文件存在：

```text
D:\work\my-driver\ckg-out\
├── entity.json
├── relation.json
├── run_meta.json
├── graph_stats.json
└── ckg.html
```

其中：

- `entity.json`：函数、变量、宏、结构体、文件等实体；
- `relation.json`：调用、读写、包含、类型、宏使用等关系；
- `run_meta.json`：源码根目录和本次构建信息；
- `ckg.html`：人工查看图谱的可视化文件。

`--graph-dir` 必须指向直接包含 `entity.json` 的目录，不能只填写工程根目录。

## 3. 先验证 MCP 服务本身

在配置 VS Code 前，先确认 MCP 服务可以启动：

```powershell
.\.venv\Scripts\python.exe -m ckg.mcp_server `
  --graph-dir "D:\work\my-driver\ckg-out"
```

服务启动后不会显示普通欢迎信息，这是正常的。因为标准输出被用于 MCP
JSON-RPC 通信，服务日志和错误应写到标准错误。

也可以确认工具定义：

```powershell
.\.venv\Scripts\python.exe -m ckg tools
```

应该包含以下三个工具：

```text
search_code_symbols
get_code_context
analyze_change_impact
```

## 4. 在 VS Code 工作区配置 MCP

### 4.1 打开 C/C++ 工程

在 VS Code 中打开需要修改的工程目录：

```text
D:\work\my-driver
```

建议将 MCP 配置放在当前工程的：

```text
D:\work\my-driver\.vscode\mcp.json
```

如果 `.vscode` 目录不存在，可以手动创建。

也可以使用 VS Code 命令面板：

```text
Ctrl+Shift+P
    -> MCP: Open Workspace Folder Configuration Files
```

### 4.2 写入 `.vscode/mcp.json`

Windows + Python 虚拟环境配置示例：

```json
{
  "servers": {
    "code-kg": {
      "type": "stdio",
      "command": "C:\\Users\\29234\\Documents\\Codex\\2026-09-16\\c-users-29234-desktop-foc-codex\\outputs\\code-kg-tool\\.venv\\Scripts\\python.exe",
      "args": [
        "-m",
        "ckg.mcp_server",
        "--graph-dir",
        "D:\\work\\my-driver\\ckg-out"
      ],
      "cwd": "C:\\Users\\29234\\Documents\\Codex\\2026-09-16\\c-users-29234-desktop-foc-codex\\outputs\\code-kg-tool"
    }
  }
}
```

注意：

- VS Code 的 `mcp.json` 使用顶层 `"servers"`；
- `"type"` 必须是 `"stdio"`；
- `"command"` 使用 Python 解释器绝对路径最稳定；
- Windows 路径中的反斜杠必须写成 `\\`；
- `"--graph-dir"` 后面填写图谱输出目录；
- `"cwd"` 填写 `code-kg-tool` 根目录，确保 Python 可以找到 `ckg` 包。

如果已经执行了全局安装：

```powershell
python -m pip install -e .
```

也可以配置为：

```json
{
  "servers": {
    "code-kg": {
      "type": "stdio",
      "command": "ckg-mcp",
      "args": [
        "--graph-dir",
        "D:\\work\\my-driver\\ckg-out"
      ],
      "cwd": "D:\\work\\my-driver"
    }
  }
}
```

但在 Windows 上建议优先使用 Python 绝对路径，因为 VS Code 启动 MCP 进程时
可能使用不同于 PowerShell 的 `PATH` 环境。

## 5. 在 VS Code 中启动和信任 MCP 服务

保存 `.vscode/mcp.json` 后，在 VS Code 中打开命令面板：

```text
Ctrl+Shift+P
    -> MCP: List Servers
```

选择 `code-kg`，然后执行：

```text
Start
```

首次启动时，VS Code 可能显示 MCP 服务信任确认。确认前检查：

- 启动命令是否指向自己的 Python；
- `--graph-dir` 是否指向自己的工程图谱；
- 服务是否只读取预期的源码和图谱目录。

确认后，打开：

```text
View
    -> Chat
```

将 Chat 模式切换为：

```text
Agent
```

在工具配置或工具选择区域确认 `code-kg` 的三个工具已启用。

VS Code 也支持在 Chat 输入框中使用 `#` 引用工具。不同版本的界面名称可能略有
变化，可以使用命令面板中的 `MCP: List Servers` 查看服务状态。

## 6. 验证工具是否可用

在 VS Code Chat 的 Agent 模式中输入：

```text
请先使用 code-kg 的 search_code_symbols 搜索 buffer_write，
再使用 get_code_context 获取它的定义、调用者、被调用者和相关源码。
暂时不要修改代码。
```

正常情况下，Agent 会调用：

```text
search_code_symbols
get_code_context
```

返回结果中应包含：

```json
{
  "target": {},
  "entities": [],
  "relations": [],
  "source_ranges": [],
  "source": []
}
```

重点检查 `source` 字段是否存在源码内容，以及源码范围是否受到
`max_files` 和 `max_lines` 限制。

## 7. 配置项目级 AI 使用规则

MCP 服务连接成功后，建议在项目中增加 AI 指令，避免 Agent 直接读取整个工程。

对于 GitHub Copilot，可以在工程中创建：

```text
.github\copilot-instructions.md
```

写入：

```markdown
# C/C++ code-kg workflow

修改 C/C++ 代码前，优先使用 code-kg MCP 工具进行结构分析。

1. 先调用 search_code_symbols 查找目标函数、变量、类型或宏。
2. 再调用 get_code_context 获取有限源码上下文。
3. 不要在没有图谱检索的情况下读取整个工程源码。
4. 修改公共函数、结构体、宏、全局变量或函数指针前，
   调用 analyze_change_impact。
5. 注意 CALLS_PTR、ASSIGNED_TO、external、dead_code 和 low_confidence
   等字段，它们表示静态分析不确定性或特殊代码路径。
6. 修改完成后重新执行 ckg build，再进行编译和测试。
7. 不要把知识图谱推断当作最终事实，最终判断以源码、编译器和测试结果为准。
```

也可以在每次请求中明确要求：

```text
请先调用 code-kg 的 search_code_symbols 和 get_code_context，
确认调用关系和数据流后再修改代码。
```

VS Code Agent 会根据可用工具和提示词决定是否调用 MCP 工具。为了提高稳定性，
建议在重要修改请求中明确写出工具名。

## 8. 推荐的代码修改流程

### 8.1 修改函数

```text
1. search_code_symbols("目标函数")
2. get_code_context("目标函数", depth=1)
3. 阅读目标函数及直接相关源码
4. 修改代码
5. analyze_change_impact("目标函数", depth=2)
6. 编译并运行相关测试
7. 重新 ckg build
```

示例请求：

```text
请给 buffer_write 增加容量检查。
先用 code-kg 查询 buffer_write 的定义、调用者、被调用者、
READS/WRITES 关系和相关结构体字段，再修改代码。
修改后分析影响范围并运行相关测试。
```

### 8.2 修改结构体或字段

重点检查：

```text
TYPE_OF
HAS_MEMBER
READS
WRITES
```

示例请求：

```text
请修改 app_ctx_t 中的状态字段。
先使用 code-kg 搜索 app_ctx_t 和字段名，
确认所有读取和写入位置，再提出修改方案。
```

### 8.3 修改宏

重点检查：

```text
USES_MACRO
MACRO_DEPENDS_ON
MACRO_ALIAS
CALLS_MACRO
```

示例请求：

```text
请分析宏 BUFFER_LOCK 的修改影响。
先查询所有展开和使用位置，不要直接全量读取工程。
```

## 9. 图谱过期处理

MCP 服务启动时读取的是指定图谱目录。AI 修改源码后，原图谱可能已经过期。

建议在修改后执行：

```powershell
.\.venv\Scripts\python.exe -m ckg build `
  "D:\work\my-driver" `
  -o "D:\work\my-driver\ckg-out"
```

然后重新让 Agent 调用：

```text
get_code_context
analyze_change_impact
```

不要继续使用修改前的图谱结果判断修改后的代码。

## 10. 常见问题

### 10.1 MCP 服务没有出现在列表中

检查：

1. 文件路径是否为当前工作区的 `.vscode/mcp.json`；
2. 顶层字段是否为 `"servers"`，而不是其他客户端使用的 `"mcpServers"`；
3. JSON 是否合法；
4. VS Code 是否已经重新加载窗口；
5. `MCP: List Servers` 中是否显示启动错误。

可以执行：

```text
Ctrl+Shift+P
    -> Developer: Reload Window
```

### 10.2 服务启动后立即退出

在 PowerShell 中直接执行配置里的完整命令：

```powershell
& "C:\path\to\python.exe" -m ckg.mcp_server `
  --graph-dir "D:\work\my-driver\ckg-out"
```

检查是否出现：

- `No module named ckg`
- `entity.json not found`
- `relation.json not found`
- Python 路径错误

### 10.3 找不到 `ckg` 模块

确认 `"cwd"` 指向：

```text
C:\Users\29234\Documents\Codex\2026-09-16\c-users-29234-desktop-foc-codex\outputs\code-kg-tool
```

或者使用虚拟环境安装：

```powershell
python -m pip install -e .
```

### 10.4 找不到图谱文件

错误示例：

```text
--graph-dir D:\work\my-driver
```

正确示例：

```text
--graph-dir D:\work\my-driver\ckg-out
```

目标目录必须直接包含：

```text
entity.json
relation.json
run_meta.json
```

### 10.5 `source` 为空

检查 `ckg-out/run_meta.json` 中的：

```json
{
  "source_root": "D:/work/my-driver"
}
```

确认 `source_root` 仍然指向当前源码目录，并且源码文件没有被移动。

### 10.6 Agent 没有自动调用知识图谱

在 Chat 中明确写出：

```text
请先调用 #search_code_symbols 和 #get_code_context，
完成图谱分析后再修改代码。
```

同时确认：

- 当前模式是 `Agent`，不是普通 Chat 或 Inline Chat；
- `code-kg` 服务已经启动；
- `code-kg` 工具在工具选择器中处于启用状态；
- 没有超过当前请求允许的工具数量。

## 11. 安全建议

当前 `code-kg` MCP 服务主要提供只读查询工具，不会通过 MCP 直接修改源码。
但 VS Code Agent 本身仍可能使用内置编辑和终端工具修改文件，因此建议：

- 首次启动时检查 MCP 服务配置并确认信任；
- 只把图谱目录配置为目标工程；
- 对公共头文件、结构体、宏和函数指针修改保留人工确认；
- 让 Agent 修改后运行编译和测试；
- 不要把包含敏感信息的目录作为源码根目录；
- 对 `external`、`low_confidence` 和 `CALLS_PTR` 结果进行人工复核。

## 12. 配置格式说明

VS Code 工作区配置使用：

```json
{
  "servers": {
    "code-kg": {
      "type": "stdio"
    }
  }
}
```

一些其他 MCP 客户端使用：

```json
{
  "mcpServers": {
    "code-kg": {}
  }
}
```

两种格式不要混用。本文针对 VS Code 的 `.vscode/mcp.json` 使用 `"servers"`。

