#ifndef CKG_EXAMPLE_ADC_H
#define CKG_EXAMPLE_ADC_H

/*
 * 示例：芯片厂商 ADC 驱动头文件桩。
 *
 * 真实工程里这类头文件通常由 SDK 提供、不在算法仓库中，但解析又需要它，
 * 否则 #include "Adc.h" 直接失败。这里只写声明，让 clang 能走完语义分析。
 * 来自本目录的符号会被当作工程内容（而不是 external 外部符号）。
 */

void Adc_Init(void);
void Adc_GetPhaseCurrentRaw(unsigned int unit, unsigned short *raw);
unsigned short Adc_GetBusVoltageRaw(unsigned int unit);

#endif /* CKG_EXAMPLE_ADC_H */
