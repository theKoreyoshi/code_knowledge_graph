# code_knowledge_graph

**零配置的 C / C++ 代码知识图谱构建工具** —— 基于 clang 真实语义 AST 与 tree-sitter 双引擎，
细到**变量**与**宏展开结果**，并自带交互式可视化。

```bash
python -m ckg build  D:/path/to/any/project
```

一条命令即可。工具自己找构建系统、自己探测编译器头文件、自己补齐缺失的声明，
输出实体 / 关系 / 宏展开 / 真实预处理代码 / 可交互图谱。

---

## 特性

- **clang 语义层解析**：直接读 clang 的 AST（类型已解析、宏已展开、引用可定位），
  因此图谱能细到变量、结构体字段、形参、枚举常量；
- **变量级数据流**：`READS` / `WRITES` / `ASSIGNED_TO` 精确到"哪个函数读了哪个全局变量的哪个字段"；
- **宏展开三层证据**：宏定义 → 展开点（含实参）→ `clang -E` 的真实预处理结果。
  `#define unit (g_units[active_unit])` 这种"看着像变量、其实是宏"的写法会被还原成
  对真实全局变量的读写，并在边上标注 `via_macro: unit`；
- **函数指针调用链解析**：`dev->init = DISPATCH(usb); … dev->init();`
  能推导出 `call_init → handler_usb`，覆盖表驱动 / ops 结构体的隐式调用；
- **零配置泛化**：自动识别 `compile_commands.json` / CMake / Make / Meson /
  **IAR `.ewp`** / **Keil `.uvprojx`**，都没有时退化为目录扫描；
  自动判定裸机与托管目标，并分别选用 shim 头文件或工具链真实 libc / libc++；
- **诊断驱动自愈**：缺 SDK 头文件时自动生成桩、合成缺失的类型与声明，多轮收敛；
- **双层交叉校验**：tree-sitter 找出 `#if 0` 等 clang 看不见的代码；
  结合 `clang -E` 的行号映射给出**死代码检测**与**逐文件解析质量分级**；
- **交互式可视化**：单文件离线 HTML，支持预设视图、类型/关系筛选、搜索、
  邻域隔离、源码与宏展开结果对照。

## 快速开始

```bash
git clone https://github.com/theKoreyoshi/code_knowledge_graph.git
cd code_knowledge_graph

python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt      # Windows
# .venv/bin/python -m pip install -r requirements.txt        # Linux / macOS

# 建图（零配置）
.venv\Scripts\python -m ckg build  D:/path/to/your/project

# 打开结果
# <项目>/ckg-out/ckg.html
```

Windows 用户也可以直接双击 `build_kg.cmd`（第一次会自动建虚拟环境并装依赖）。

仓库自带两个可直接试跑的示例工程：

```bash
python -m ckg build examples/demo-c      # C11 + CMake
python -m ckg build examples/demo-cpp    # C++17 + CMake（模板 / 继承 / STL）
```

## 输出物

| 文件 | 内容 |
| --- | --- |
| `entity.json` | 实体：文件 / 宏 / 函数 / 方法 / 类 / 结构体 / 联合体 / 枚举 / typedef / 命名空间 / 变量 / 局部变量 / 形参 / 字段 |
| `relation.json` | 关系：调用、读写、赋值、类型、继承、宏使用与依赖…… |
| `macro_expansions.json` | 每处宏展开点（schema 与 [`code_kg_with_tree-sitter`](https://github.com/Garden1a-X/code_kg_with_tree-sitter) 的 `macro.json` 兼容） |
| `macro_expansions_detailed.json` | 展开点 + 实参 + 推导展开文本 + `clang -E` 真实输出 |
| `preprocessed/*.i` | 宏全部展开后的真实代码 |
| `ckg.html` | 交互式图谱（离线单文件） |
| `graph.graphml` | 可导入 Gephi / yEd / Neo4j |
| `报告.md` | 中文分析报告（热点函数、宏热点、死代码、未调用函数） |

### 关系类型

`CONTAINS` `INCLUDES` `CALLS` `CALLS_PTR` `CALLS_MACRO` `READS` `WRITES` `ASSIGNED_TO`
`HAS_PARAMETER` `HAS_VARIABLE` `HAS_MEMBER` `HAS_METHOD` `TYPE_OF` `RETURNS`
`TYPEDEF_OF` `INHERITS` `USES_MACRO` `MACRO_DEPENDS_ON` `MACRO_ALIAS` `GENERATES`

### 可信度标注

工具不会假装什么都能解析，所有"推测出来的内容"都标了出来：

| 标记 | 含义 |
| --- | --- |
| `external` | 来自 SDK / 标准库，不在工程源码内 |
| `auto_generated` | 由诊断修复自动合成的声明 |
| `dead_code` | 文本存在但预处理器没有编译（`#if 0` 等） |
| `low_confidence` | 所在文件解析错误较多，结论需人工确认 |
| `quality` | 每个文件的解析质量：`full` / `partial` / `degraded` |

## 算法一览

```
  项目目录 ──► 1 构建系统识别（compile_commands / CMake / Make / Meson / IAR / Keil / 扫描）
                     │
                     ▼
               2 编译器探测（解析 clang -E -v 的搜索路径与 -cc1 的 -D / -isystem）
                     │
                     ▼
               3 诊断驱动自愈（缺头文件/类型/函数/变量 → 自动补桩，多轮收敛）
                     │
        ┌────────────┴────────────┐
        ▼                         ▼
   4 clang 语义抽取            7 tree-sitter 交叉校验
     实体 / 关系 / 位置双视图     死代码发现 / 兜底 / 覆盖率
        ▼                         ▼
   5 宏展开引擎               6 clang -E 真实预处理 + 行号映射
        └────────────┬────────────┘
                     ▼
              8 图构建 / 导出 / 交互式可视化
```

详细原理见 **[docs/02-解析算法详解.md](docs/02-解析算法详解.md)**。

## 验证

对 5 个形态完全不同的工程执行同一条零配置命令，共 30 项断言全部通过：

| 用例 | 形态 | 自动识别 | 实体 / 关系 | 断言 |
|---|---|---|---|---|
| `01-demo-c-cmake` | C11 + CMake | `cmake` | 163 / 357 | 8/8 |
| `02-demo-cpp-cmake` | C++17 + CMake（模板 / 继承 / STL） | `cmake` | 322 / 266 | 7/7 |
| `03-embedded-autorepair` | 嵌入式 C，无构建文件，缺厂商头文件 | 判定裸机 + 自动补桩 | 86 / 169 | 7/7 |
| `04-compile-commands` | 带 `compile_commands.json`（运行时生成） | `compile_commands` | 163 / 357 | 3/3 |
| `05-large-generated` | 300 个函数的大文件（运行时生成） | 目录扫描 | 909 / 3910 | 4/4 |

用例 01–03 使用仓库自带示例，04–05 由脚本在运行时生成，因此在任何机器上都能复现。

复现：

```bash
python tools/validate.py
```

完整报告见 [docs/04-验证报告.md](docs/04-验证报告.md)。

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/01-使用教程.md](docs/01-使用教程.md) | 安装、建图、看懂输出、界面导览、配置项、各类工程处理方式、查询与二次利用、FAQ |
| [docs/02-解析算法详解.md](docs/02-解析算法详解.md) | 构建系统识别、编译器探测、自动修复、AST 遍历、读写判定、宏展开三层、死代码检测、并行与性能 |
| [docs/03-扩展开发指南.md](docs/03-扩展开发指南.md) | 加一种关系 / 实体 / 语言 / 构建系统，换前端，自定义可视化，回归清单 |
| [docs/04-验证报告.md](docs/04-验证报告.md) | 5 个工程的泛化验证结果与断言明细 |

## 命令行

```bash
python -m ckg init  <path>              # 只做识别，打印/写出配置
python -m ckg build <path>              # 零配置建图
python -m ckg build -c my.json -o out    # 用配置文件建图
python -m ckg build <path> -j 8          # 8 线程并行
python -m ckg query <符号> -d out        # 查某个函数/变量/宏的上下游
python -m ckg context <符号> -d out --format json # 输出 AI 使用的受限上下文
python -m ckg impact <符号> -d out --format json  # 分析修改影响范围
python -m ckg tools                       # 导出 AI 工具定义
python -m ckg.mcp_server --graph-dir out    # 以 MCP stdio 服务启动
python -m ckg viz -d out                 # 只重新生成 HTML
python -m ckg info                       # 显示识别到的工具链
```

AI 集成和工具调用流程见 [docs/05-AI检索集成.md](docs/05-AI检索集成.md)。

## 目录结构

```
code_knowledge_graph/
├── ckg/                    解析算法与可视化（见 docs/03 代码地图）
│   ├── autoconfig.py       构建系统识别
│   ├── toolchain.py        编译器探测与目标决策
│   ├── shimgen.py          诊断驱动自愈
│   ├── extract_clang.py    clang 语义抽取
│   ├── macro_engine.py     宏展开引擎
│   ├── extract_treesitter.py
│   ├── preprocess.py       clang -E 与行号映射
│   ├── parallel_extract.py 并行抽取
│   ├── graph_builder.py    图存储与导出
│   ├── visualize.py + templates/
│   └── shims/              标准 C 头文件桩（裸机用）
├── docs/                   教程与算法文档
├── examples/
│   ├── demo-c/             可直接跑通的 C 示例
│   ├── demo-cpp/           可直接跑通的 C++ 示例
│   └── vendor-shims/       参考：为嵌入式工程手写的厂商头文件桩
├── tools/validate.py       泛化能力回归测试
├── pyproject.toml
└── requirements.txt
```

## 环境要求

- Python ≥ 3.9
- `libclang`（wheel 自带动态库，**不需要**安装完整 LLVM）
- `tree-sitter` + `tree-sitter-c`
- 可选 `ziglang`：提供 clang 驱动与完整 C/C++ 标准头文件，Windows 上省去配置编译器的麻烦
- 可选系统 `clang` / `gcc`：存在则优先使用

`python -m ckg info` 可打印当前识别到的工具链与头文件搜索路径。

## 许可

MIT，见 [LICENSE](LICENSE)。
