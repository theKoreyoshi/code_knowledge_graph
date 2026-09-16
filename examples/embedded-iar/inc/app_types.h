#ifndef APP_TYPES_H
#define APP_TYPES_H

#include <stdint.h>
#include "app_config.h"

typedef enum {
    APP_OK = 0,
    APP_ERR_ARG = -1,
    APP_ERR_FAULT = -2
} app_status_t;

typedef struct {
    float    target;
    float    feedback;
    uint16_t fault;
    uint8_t  state;
} app_channel_t;

typedef struct {
    app_channel_t channel[APP_MOTOR_COUNT];
    uint32_t      last_tick_us;
    uint32_t      loop_count;
} app_ctx_t;

typedef app_status_t (*app_handler_t)(app_ctx_t *ctx, void *user);

#endif /* APP_TYPES_H */
