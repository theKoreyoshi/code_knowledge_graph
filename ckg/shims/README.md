# 标准 C 头文件桩（裸机目标专用）

当解析目标没有 C 库时（例如 `arm-none-eabi` 这类裸机三元组），
编译器工具链里不存在 `stdint.h`、`stdbool.h` 等头文件，解析会在第一行 `#include` 就失败。

这个目录提供**最小可用**的标准头文件：

```
stdint.h    定宽整型与 INT32_MAX 之类的宏（按 ILP32 模型定义，适配 32 位 MCU）
stdbool.h   bool / true / false
stddef.h    size_t / ptrdiff_t / NULL / offsetof
string.h    memcpy / memset / strlen 等声明
math.h      sinf / cosf / sqrtf 等声明
```

它们**只有声明**，不会被链接、也不会被执行，唯一作用是让 clang 走完语义分析、
产出完整 AST。

## 什么时候会用到

`ckg/toolchain.py` 的 `is_hosted()` 会检查工具链自带的 include 目录里
是否存在 `stdio.h` / `stdlib.h` / `string.h`：

- **托管目标**（Linux / Windows / macOS）：使用工具链自带的真实 libc，
  **本目录被自动禁用**，避免 `size_t`、`intptr_t` 之类的重定义冲突；
- **裸机目标**：启用本目录。

所以不需要手工开关，配置里写 `"target": "arm-none-eabi"` 就会自动生效。

## 缺少的是厂商头文件怎么办

标准头文件之外，工程往往还依赖芯片厂商 / 驱动的头文件（如 `Adc.h`、`pwm.h`）。
这类**工程专属**的桩放在工程自己的目录里，通过配置的 `shim_include_dirs` 引入，
例如仓库中的 `examples/vendor-shims/`。

两者的区别：

| 配置字段 | 用途 | 生效条件 |
| --- | --- | --- |
| `std_shim_dirs` | 标准 C 头文件桩（本目录） | 仅裸机目标 |
| `shim_include_dirs` | 工程专属厂商头文件桩 | 始终生效 |

如果懒得手写厂商桩，也可以什么都不做：`ckg/shimgen.py` 会根据 clang 的诊断
自动生成缺失头文件的空桩与缺失声明的合成体（写在输出目录的 `_auto_shim/`）。
