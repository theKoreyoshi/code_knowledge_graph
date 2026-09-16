#ifndef APP_API_H
#define APP_API_H

#include <stddef.h>
#include <stdint.h>

#include "app_types.h"

void          App_SelectUnit(uint8_t unit);
void          App_Run(float target);
uint16_t      App_FaultCount(void);
uint32_t      App_LoopCount(void);
app_status_t  App_Dispatch(const app_handler_t *table, size_t count, void *user);

#endif /* APP_API_H */
