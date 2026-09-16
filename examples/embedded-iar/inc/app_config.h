#ifndef APP_CONFIG_H
#define APP_CONFIG_H

#include <stdint.h>

/* 编译期常量与函数式宏（嵌套展开：APP_CLAMP 里再套 APP_SCALE 等） */
#define APP_MOTOR_COUNT   2u
#define APP_CLAMP(x, lo, hi)  (((x) < (lo)) ? (lo) : (((x) > (hi)) ? (hi) : (x)))
#define APP_SCALE(v)          ((v) * 2.0f)
#define APP_MASK(n)           ((n) & 0x03u)

/*
 * 用宏模拟"全局上下文变量"：这是嵌入式代码里很常见的写法，
 * 也是普通文本解析工具最容易看错的写法。
 */
#define app  (g_app_ctx[g_active_unit].channel[0])

/* 只在旧版本中使用的开关，用于演示 #if 0 死代码检测 */
#define APP_LEGACY_PATH 0

#endif /* APP_CONFIG_H */
