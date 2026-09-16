#ifndef CKG_EXAMPLE_ICU_H
#define CKG_EXAMPLE_ICU_H

/* 示例：输入捕获（霍尔 / 编码器）驱动头文件桩（声明-only）。 */

void Icu_EnableNotification(unsigned int pin);
void Icu_DisableNotification(unsigned int pin);
void Icu_SetUserIsr(unsigned int pin, void (*callback)(void));

#endif /* CKG_EXAMPLE_ICU_H */
