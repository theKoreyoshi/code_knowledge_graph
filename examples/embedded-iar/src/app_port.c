#include "app_api.h"
#include "app_config.h"

/*
 * 下面几个头文件由芯片厂商 SDK 提供，通常不在算法仓库里。
 * 不写任何桩也有用：工具会读 clang 的诊断自动生成它们。
 */
#include "Adc.h"
#include "pwm.h"
#include "Icu.h"
#include "PinCfg.h"

#if defined(__ICCARM__)
/* IAR 专有：中断向量由启动文件注册（这里只是标记编译器） */
#endif

/* 引脚宏来自芯片头文件；缺失时会被自动补成 extern 声明 */
#define APP_HALL_U  P14_01
#define APP_HALL_V  P16_01
#define APP_HALL_W  P16_02

static uint16_t s_last_duty[APP_MOTOR_COUNT];

static void App_HallCallback(void)
{
    App_SelectUnit(0u);
}

void App_PortInit(void)
{
    Adc_Init();
    Pwm_Init();

    Icu_SetUserIsr(APP_HALL_U, App_HallCallback);
    Icu_SetUserIsr(APP_HALL_V, App_HallCallback);
    Icu_SetUserIsr(APP_HALL_W, App_HallCallback);

    Icu_EnableNotification(APP_HALL_U);
    App_SelectUnit(0u);
}

void App_PortOutput(uint16_t duty_a, uint16_t duty_b, uint16_t duty_c)
{
    s_last_duty[0] = duty_a;
    Pwm_SetDuty(0u, duty_a, duty_b, duty_c);
}

uint16_t App_PortLastDuty(void)
{
    return s_last_duty[0];
}

void App_PortStop(void)
{
    Pwm_Disable(0u);
}
