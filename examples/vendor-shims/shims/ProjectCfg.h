#ifndef CKG_EXAMPLE_PROJECTCFG_H
#define CKG_EXAMPLE_PROJECTCFG_H

/*
 * 示例：工程 / 芯片配置头文件桩。
 *
 * 真实工程里 P14_01 这类引脚宏来自芯片头文件。完全缺失时，
 * `Icu_SetUserIsr(P14_01, cb)` 会报 "use of undeclared identifier"。
 * 这里给出占位声明即可；也可以什么都不写，交给工具自动合成
 * （ckg/shimgen.py 会生成 extern int 声明）。
 */

extern const unsigned int P10_00;
extern const unsigned int P10_01;
extern const unsigned int P14_01;
extern const unsigned int P14_02;
extern const unsigned int P16_01;
extern const unsigned int P16_02;

#endif /* CKG_EXAMPLE_PROJECTCFG_H */
