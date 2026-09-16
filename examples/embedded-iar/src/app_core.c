#include "app_api.h"

#include <string.h>

/* 模块私有状态：全局变量 + 数据流分析的观察对象 */
static app_ctx_t g_app_ctx[APP_MOTOR_COUNT];
static uint8_t   g_active_unit = 0u;
static uint32_t  g_loop_count = 0u;
static uint16_t  g_fault_count = 0u;

static float App_ClampOutput(float value)
{
    return APP_CLAMP(value, 0.0f, 100.0f);
}

void App_SelectUnit(uint8_t unit)
{
    if (unit < APP_MOTOR_COUNT) {
        g_active_unit = unit;
    }
}

void App_Run(float target)
{
    float scaled;

    scaled = APP_SCALE(App_ClampOutput(target));

    /* 下面三行的左值都写作 app.xxx，实际写的是 g_app_ctx / g_active_unit */
    app.target = scaled;
    app.feedback = app.target * 0.9f;
    g_loop_count++;
    g_app_ctx[g_active_unit].loop_count = g_loop_count;

    if (app.feedback > 90.0f) {
        g_fault_count++;
        app.fault = (uint16_t)APP_MASK(app.fault + 1u);
        app.state = 3u;
    }
}

uint16_t App_FaultCount(void)
{
    return g_fault_count;
}

uint32_t App_LoopCount(void)
{
    return g_loop_count;
}

app_status_t App_Dispatch(const app_handler_t *table, size_t count, void *user)
{
    size_t i;

    if (!table) {
        return APP_ERR_ARG;
    }
    for (i = 0u; i < count; i++) {
        if (table[i]) {
            app_status_t status = table[i](g_app_ctx, user);
            if (status != APP_OK) {
                return status;
            }
        }
    }
    return APP_OK;
}

#if 0
/*
 * 这段代码在文本里真实存在，但预处理器从不编译它。
 * clang 的 AST 里完全没有这些符号，只有 tree-sitter 能看到。
 */
static void App_LegacyRun(float target)
{
    app.target = target;
    APP_LEGACY_PATH;
}

static uint16_t App_LegacyFaults(void)
{
    return g_fault_count;
}
#endif
