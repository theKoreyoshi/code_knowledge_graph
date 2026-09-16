#ifndef CKG_EXAMPLE_PWM_H
#define CKG_EXAMPLE_PWM_H

/* 示例：PWM 驱动头文件桩（声明-only）。 */

void Pwm_Init(void);
void Pwm_SetDuty(unsigned int unit, unsigned short duty_a,
                 unsigned short duty_b, unsigned short duty_c);
void Pwm_Disable(unsigned int unit);

#endif /* CKG_EXAMPLE_PWM_H */
